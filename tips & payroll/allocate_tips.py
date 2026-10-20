"""Allocate each Gateway store tip pool to non-management crew in decimal hours.

Inputs are verified, normalized JSON documents with matching pay periods. This
module does not infer ADP employee identifiers or submit payroll to ADP.
"""

import argparse
import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path


CREW = {"crew", "crew member", "crew leader"}
MANAGEMENT = {"manager", "co-manager", "assistant manager", "manager in training"}


def cents(value, label):
    """Parse nonnegative amounts/hours exactly, with at most two decimals."""
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"Invalid {label}.") from None
    if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
        raise ValueError(f"{label} must be nonnegative with at most two decimal places.")
    return int(amount * 100)


def decimal_text(value):
    return f"{Decimal(value) / 100:.2f}"


def store_id(value):
    match = re.fullmatch(r"(\d+)-(\d+)", str(value).strip())
    if not match:
        raise ValueError("Store must include franchise and store number, e.g. 3356-01.")
    return f"{int(match[1])}-{int(match[2]):02d}"


def period(document):
    value = document.get("period", {})
    try:
        start = date.fromisoformat(value["start_date"])
        end = date.fromisoformat(value["end_date"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Both sources need explicit start_date and end_date.") from None
    if end < start:
        raise ValueError("Period end precedes start.")
    return {"start_date": start.isoformat(), "end_date": end.isoformat()}


def allocate(gateway, payroll):
    dates = period(gateway)
    if dates != period(payroll):
        raise ValueError("Gateway tips and payroll hours cover different dates.")
    if payroll.get("verified") is not True or payroll.get("issues"):
        raise ValueError("Payroll hours must be verified and have no unresolved issues.")
    if gateway.get("verified") is not True or gateway.get("issues"):
        raise ValueError("Gateway totals must be verified and have no unresolved issues.")

    pools = {}
    for row in gateway.get("stores", []):
        sid = store_id(row["store"])
        if sid in pools:
            raise ValueError(f"Duplicate Gateway store: {sid}")
        pools[sid] = cents(row["total_tips"], "store tips")
    if not pools:
        raise ValueError("No Gateway store totals supplied.")

    rows, seen, manager_ids = [], set(), set()
    for row in payroll.get("employees", []):
        sid = store_id(row["store"])
        eid = str(row["employee_id"]).strip()
        if not eid:
            raise ValueError("Missing employee ID.")
        if (sid, eid) in seen:
            raise ValueError(f"Duplicate employee/store hours: {sid}, {eid}")
        seen.add((sid, eid))
        if sid not in pools:
            raise ValueError(f"Missing Gateway total for store {sid}.")
        raw_roles = row.get("roles")
        if not isinstance(raw_roles, list) or not raw_roles:
            raise ValueError(f"Missing role classification for employee {eid}.")
        roles = {str(role).strip().lower() for role in raw_roles}
        hours = cents(row["total_hours"], "decimal hours")
        # A roster-only person with no work can remain visible without guessing
        # a tip-eligible title. Any worked hours still require classification.
        unknown = roles - CREW - MANAGEMENT
        if roles == {"unassigned"} and hours == 0:
            unknown = set()
        if unknown:
            raise ValueError(f"Unclassified role for employee {eid}: {', '.join(sorted(unknown))}")
        if roles & MANAGEMENT:
            manager_ids.add(eid)
        rows.append({"store": sid, "employee_id": eid, "name": str(row.get("name", "")),
                     "roles": sorted(roles), "hours_cents": hours})

    by_store = defaultdict(list)
    for row in rows:
        # The user excludes managers as people, including anyone appearing under
        # a management role elsewhere in the supplied payroll period.
        row["eligible"] = row["employee_id"] not in manager_ids and bool(set(row["roles"]) & CREW)
        row["tip_cents"] = 0
        by_store[row["store"]].append(row)

    store_results = []
    for sid, pool in sorted(pools.items()):
        eligible = [row for row in by_store[sid] if row["eligible"] and row["hours_cents"] > 0]
        total_hours = sum(row["hours_cents"] for row in eligible)
        if pool and not total_hours:
            raise ValueError(f"Store {sid} has tips but no eligible crew hours.")
        if total_hours:
            for row in eligible:
                exact_cents = Fraction(pool * row["hours_cents"], total_hours)
                row["tip_cents"] = exact_cents.numerator // exact_cents.denominator
                row["fraction"] = exact_cents - row["tip_cents"]
            remainder = pool - sum(row["tip_cents"] for row in eligible)
            # Largest fractional remainders receive the leftover pennies, with
            # employee ID as a stable tie-breaker. Every store reconciles exactly.
            ranked = sorted(eligible, key=lambda row: (-row["fraction"], row["employee_id"]))
            for row in ranked[:remainder]:
                row["tip_cents"] += 1
        allocated = sum(row["tip_cents"] for row in by_store[sid])
        if allocated != pool:
            raise ValueError(f"Store {sid} allocation did not reconcile.")
        store_results.append({"store": sid, "gateway_tips": decimal_text(pool),
                              "eligible_crew_hours": decimal_text(total_hours),
                              "allocated_tips": decimal_text(allocated)})

    employee_results = []
    for row in sorted(rows, key=lambda row: (row["store"], row["employee_id"])):
        employee_results.append({"store": row["store"], "employee_id": row["employee_id"],
                                 "name": row["name"], "roles": row["roles"], "eligible": row["eligible"],
                                 "total_hours": decimal_text(row["hours_cents"]),
                                 "eligible_hours": decimal_text(row["hours_cents"] if row["eligible"] else 0),
                                 "tips": decimal_text(row["tip_cents"])})
    return {"period": dates, "hours_format": "decimal, two places", "stores": store_results,
            "employees": employee_results,
            "rounding": "Whole cents, largest fractional remainder first; store totals reconcile exactly."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("gateway", type=Path)
    parser.add_argument("payroll", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = allocate(json.loads(args.gateway.read_text(encoding="utf-8")),
                      json.loads(args.payroll.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Allocated tips for {len(result['stores'])} stores; every store reconciles.")
