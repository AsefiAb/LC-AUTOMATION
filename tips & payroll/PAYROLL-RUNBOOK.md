# Payroll reporting without ADP

GitHub Actions **Biweekly payroll reports** connects Gateway Financial View tips
and TCP calculated hours. The pipeline writes 18 store workbooks, a combined
workbook with All employees and Managers sheets, and Alicia's email with both
tables and all workbooks attached. It does not access or import into ADP.

## Secret names in LC-AUTOMATION

| Source | Secrets |
| --- | --- |
| TCP (already present) | `TCP_CLIENT_ID`, `TCP_CLIENT_SECRET`, `TCP_API_KEY` |
| Colorado Gateway | `CO_GATEWAY_USER`, `CO_GATEWAY_PASS` |
| Arizona Gateway | `AZ_GATEWAY_USER`, `AZ_GATEWAY_PASS` |
| Texas Gateway | `TX_GATEWAY_USER`, `TX_GATEWAY_PASS` |
| Gmail (already present) | `CAAZ_GMAIL_APP_PASS` |

Altametrics secrets are for sales only. GitHub cannot reveal secret values from
the old tips-yolo repo; add Gateway values here from the owner's credential store.
No Microsoft or ADP credentials are required for payroll reports.

## Calculation and checks

- Crew, Crew Member and Crew Leader receive tips. Anyone with a management title
  anywhere in the period is excluded from every store's eligible hours and tips.
- Per store: Gateway tips / eligible decimal crew hours * employee decimal hours.
  Overtime hours count as actual hours, without a 1.5 multiplier.
- Allocate whole cents by largest fractional remainder so each store's tips
  reconcile exactly. The displayed rate is informational; calculations use the
  full ratio rather than a rounded $1.31 rate.
- TCP regular, overtime1 and overtime2 classifications supply worked hours.
  Leave is excluded. Do not recalculate OT from an 80-hour two-week threshold.
- Group managers and the full employee list by GEID across stores. Names alone
  are not identifiers. Blank Payroll IDs remain blank while ADP is on hold.
- Include the current TCP roster and people with worked hours during the period;
  zero-hour staff need a verified default job assignment and home store. Unknown
  roles, missing names/stores, open punches, unmatched segments and closed shifts
  with zero calculated hours stop delivery and appear in a private issue report.
- Total and OT are rounded to hundredths; displayed regular is Total minus OT
  so columns add up. This can differ by .01 from separately rounding regular.
  Exact source minutes remain in private audit JSON. Hours are never hh:mm.

The current roster/default assignment API is not a historical personnel audit.
Backfills can flag changed/terminated employee mappings and must resolve those
issues before delivery. No unresolved classifications are silently omitted.

## Dates, output and email

The first scheduled run is September 22, 2026, targeting September 8–21 inclusive.
Subsequent runs are every 14 days, Tuesday at 08:00 America/Los_Angeles.
Examples: `3356-01 9.8.26-9.21.26.xlsx`, then
`3356-01 9.22.26-10.5.26.xlsx`. Every store uses its own number.

Gateway uses the same inclusive dates. TCP shifts belong to their start date;
overnight shifts starting within the period are included in full.

From **ab@caazllc.com**, to **alicia@lccollc.com**. The email shows managers combined
across stores, followed by everyone, with regular hours, OT, total hours and tips.
Managers in both views are counted once in overall totals.

## Run and activate

1. Use `preflight` to check live TCP/Gateway without email. Blank dates choose the
   latest completed scheduled period; manual dates must be a completed 14 days.
2. Use `preview` to create all workbooks and email HTML privately, without sending.
3. After the replacement reconciles, disable the old tips-yolo workflow for the
   controlled cutover and run `deliver`. Confirm receipt before archiving the old
   repo. Keep it for rollback if any stage fails.
4. Set repository variable `PAYROLL_AUTOMATION_ENABLED=true` for scheduled runs.

GitHub jobs may start late. UTC triggers cover both Pacific DST offsets and one
catch-up attempt; only the correct alternate Tuesday, at or after 08:00, runs.
The `payroll-delivery-state` branch records the period and prevents duplicates.
An uncertain send requires checking Gmail Sent before manually clearing a receipt.

Private artifacts retain source audit data, mapping issues and generated reports
for three days. The workflow fails on unresolved issues rather than emailing
partial payroll. Production delivery remains disabled until live input checks and
the first real preview succeed. Gateway browser controls still require live
verification with the separate Gateway credentials.

## Live TCP check, September 20, 2026

The September 6–19 diagnostic period returned 2,002 calculated segments matching
2,002 work segments. It identified 430 roster employees, including zero-hour
staff, and 438 employee/store rows. Delivery was correctly blocked by:

- Three open punches and 148 closed shifts with zero calculated hours.
- No calculated worked shifts on September 6, 7, or 14.
- GEID 6584508 (Maximo Cano, 3759-04) has no classifiable active job assignment.
- At the time of that diagnostic run, Gateway secrets were missing. All six
  have since been added, and live login reached the regional reporting pages.
  Report extraction is being verified separately.

The August 25–September 7 historical query returned no calculated worked shifts.
This is unavailable data, not zero payroll. Confirm the historical hours access
with TCP; resolve/recalculate the flagged shifts and assign the missing job title.
Each preflight now writes a private `TCP corrections <period>.csv` with the exact
GEIDs, stores and segment IDs to review. No payroll email has been sent.

[Live TCP check](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35535706204).

Run tests: `python -m unittest discover -s "tips & payroll" -p "test_*.py" -v`.
