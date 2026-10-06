"""Prepare auditable, store/employee/shift summaries from a private TCP report."""

import argparse
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path


def minutes(value):
    if value is None:
        raise ValueError("Missing calculated hours; cannot substitute zero.")
    text = str(value)
    if ":" in text:
        hours, mins = text.split(":")
        if not (0 <= int(mins) < 60):
            raise ValueError("Invalid minutes in TCP duration.")
        return int(hours) * 60 + int(mins)
    result = Decimal(text) * 60
    return float(result)


def store_label(value):
    parts = (value or "").split("-")
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        return f"{int(parts[0])}-{int(parts[1]):02d}"
    return value or "Unmapped"


def summarize(report):
    required = ("locations", "jobcodes", "employees", "hours", "worksegments")
    data = {}
    for kind in required:
        dataset = report["datasets"].get(kind)
        if not dataset or not dataset.get("complete"):
            raise ValueError(f"Incomplete required dataset: {kind}")
        data[kind] = dataset["records"]
    if report.get("errors"):
        raise ValueError("Inspection has errors; resolve them before summarizing.")
    jobs = {str(row["jobCodeId"]): row for row in data["jobcodes"]}
    start, end = report["period"]["startDate"], report["period"]["stopDate"]
    segments, seen = [], set()
    people = {}
    open_counts = Counter()
    by_person = defaultdict(lambda: [0, 0, 0])
    for person in data["hours"]:
        emp = person["employee"]
        eid = str(emp["employeeId"])
        people[eid] = emp.get("employeeName") or f"Employee {eid}"
        for segment in person["segments"]:
            if not start <= (segment.get("timeIn") or "")[:10] <= end:
                continue
            sid = str(segment["recordId"])
            if sid in seen:
                raise ValueError(f"Duplicate calculated segment: {sid}")
            seen.add(sid)
            job_id = str(segment.get("jobCode", {}).get("jobCodeId", ""))
            job = jobs.get(job_id, {})
            store = store_label(job.get("locationName"))
            role = (job.get("description") or "Unmapped").split(" - ")[0]
            open_punch = not segment.get("timeOut")
            leave = bool(segment.get("isLeave") or job.get("leaveCode"))
            category = "Open punch" if open_punch else ("Leave" if leave else "Worked")
            vals = [minutes(segment.get("hours", {}).get(key)) for key in ("regular", "overtime1", "overtime2")]
            # TCP also returns monetary tipping markers in the hours endpoint.
            # A known Tipping code with equal punches and zero calculated time
            # is not worked time or an employee title. Keep the segment/ID in
            # the audit, but never add its money to the Gateway tip pool.
            if (category == "Worked" and role.casefold() == "tipping" and not sum(vals)
                    and segment.get("timeIn") == segment.get("timeOut")):
                category = "Non-work adjustment"
            if category == "Worked" and not sum(vals) and segment["timeOut"] > segment["timeIn"]:
                category = "Zero-hour shift"
            segments.append({"segment_id": sid, "employee_id": eid, "name": people[eid], "store": store,
                             "role": role, "time_in": segment.get("timeIn"), "time_out": segment.get("timeOut"),
                             "regular_minutes": vals[0], "ot1_minutes": vals[1], "ot2_minutes": vals[2], "category": category})
            if category in ("Worked", "Zero-hour shift"):
                for index, value in enumerate(vals):
                    by_person[(store, eid)][index] += value
            elif category == "Open punch":
                open_counts[(store, eid)] += 1
    keys = sorted(set(by_person) | set(open_counts))
    rows = []
    for store, eid in keys:
        roles = sorted({s["role"] for s in segments if s["store"] == store and s["employee_id"] == eid and s["category"] not in ("Leave", "Non-work adjustment")})
        vals = by_person[(store, eid)]
        rows.append({"store": store, "employee_id": eid, "name": people[eid], "roles": "; ".join(roles),
                     "regular_minutes": vals[0], "ot1_minutes": vals[1], "ot2_minutes": vals[2], "open_punches": open_counts[(store, eid)]})
    stores = []
    known_stores = {store_label(x["name"]) for x in data["locations"]}
    for store in sorted(known_stores | {x["store"] for x in rows}):
        persons = [x for x in rows if x["store"] == store]
        stores.append({"store": store, "people": len(persons),
                       **{key: sum(x[key] for x in persons) for key in ("regular_minutes", "ot1_minutes", "ot2_minutes", "open_punches")},
                       "tips": None, "tip_status": "Not returned"})
    work_ids = {str(x["id"]) for x in data["worksegments"] if start <= (x.get("timeIn") or "")[:10] <= end}
    tracked = [x for x in data["worksegments"] if any(x.get(key) is not None for key in ("tracked1", "tracked2", "tracked3"))]
    tip_entries = [x for x in data["worksegments"] if jobs.get(str(x.get("jobCodeId")), {}).get("description", "").lower() == "tipping"]
    # This report deliberately leaves monetary totals unavailable until the tip
    # field and store attribution are verified, even if future samples contain values.
    return {"period": report["period"], "generated_at": report["generated_at"], "stores": stores,
            "employees": rows, "segments": sorted(segments, key=lambda x: (x["store"], x["employee_id"], x["time_in"])),
            "checks": {"calculated_segments": len(segments), "work_segments": len(work_ids),
                       "hours_only_ids": sorted(seen - work_ids), "work_only_ids": sorted(work_ids - seen),
                       "people": len({x["employee_id"] for x in rows}), "open_punches": sum(open_counts.values()),
                       "zero_hour_closed_shifts": sum(x["category"] == "Zero-hour shift" for x in segments),
                       "tip_entries": len(tip_entries), "tracked_value_entries": len(tracked),
                       "returned_shift_dates": sorted({x["time_in"][:10] for x in segments})},
            "historical_tip_sample": report.get("historical_tip_sample", {})}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = summarize(json.loads(args.source.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"checks": result["checks"], "stores": result["stores"]}, indent=2))
