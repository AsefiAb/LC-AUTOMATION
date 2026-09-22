# Payroll reporting without ADP

GitHub Actions **Biweekly payroll reports** connects Gateway Financial View tips
and TCP calculated hours. The pipeline writes 18 store workbooks, a combined
workbook with All employees and Managers sheets, and Alicia's email with both
tables and all workbooks attached. It does not access or import into ADP.

The separate [ADP CSV builder](ADP-EXPORT.md) prepares the original regional
import format once fresh exports, verified employee mappings and final payroll
inputs are available. It never submits an import or sends an email.

## Secret names in LC-AUTOMATION

| Source | Secrets |
| --- | --- |
| TCP (already present) | `TCP_CLIENT_ID`, `TCP_CLIENT_SECRET`, `TCP_API_KEY` |
| Colorado Gateway | `CO_GATEWAY_USER`, `CO_GATEWAY_PASS` |
| Arizona Gateway | `AZ_GATEWAY_USER`, `AZ_GATEWAY_PASS` |
| Texas Gateway | `TX_GATEWAY_USER`, `TX_GATEWAY_PASS` |
| Gmail (already present) | `CAAZ_GMAIL_APP_PASS` |

All TCP, Gateway, and Gmail secrets are present. Gateway extraction was verified
for all 18 stores on September 20. Altametrics secrets are for sales only.
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
- Include the current TCP roster and people with worked hours during the period.
  People with no worked shifts and no role remain UNASSIGNED at zero hours/tips,
  with a setup warning; without a home store they appear in the combined roster.
  Unknown roles on worked shifts, missing names or worked-store mappings, open
  punches, unmatched segments and elapsed shifts with zero calculated hours stop
  delivery and appear in a private issue report.
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

GitHub jobs may start late. America/Los_Angeles triggers cover daylight saving
time and catch-up attempts; only the correct alternate Tuesday, at or after 08:00, runs.
The `payroll-delivery-state` branch records the period and prevents duplicates.
An uncertain send requires checking Gmail Sent before manually clearing a receipt.

Private artifacts retain source audit data, mapping issues and generated reports
for three days. The workflow fails on unresolved issues rather than emailing
partial payroll. Scheduled delivery was enabled on September 22 after a completed
local reconciliation and Excel preview. Gateway extraction and Gmail authentication
have passed live checks. The API calculation discrepancy still blocks unattended
completion whenever TCP returns missing calculated hours; see the latest note below.

## Live TCP check, September 20, 2026

The latest September 6–19 diagnostic returned 2,004 calculated segments matching
2,004 work segments. Delivery was correctly blocked by:

- Three open punches and 148 closed shifts with zero calculated hours.
- No calculated worked shifts on September 6, 7, or 14.
- GEID 6584508 (Maximo Cano, 3759-04) has no classifiable active job assignment.
- Gateway tips were extracted successfully for all 18 stores.

The August 25–September 7 historical query returned no calculated worked shifts.
This is unavailable data, not zero payroll. Confirm historical hours access
with TCP. Do not re-enter the 148 closed shifts: TCP's UI and native report have
their calculated hours; the API returned zero. Actual open punches still require
verified clock-out times, and the missing employee title still needs confirmation.
Each preflight now writes a private `TCP corrections <period>.csv` with the exact
GEIDs, stores and segment IDs to review. No payroll email has been sent.

[Live TCP/Gateway check](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35539807831).

## Verified manual fallback for the API discrepancy

On September 20, the Complete Payroll OpenXML export for September 14 matched all
148 API shifts by GEID, job code, clock-in and clock-out. Its calculated regular,
OT1 and OT2 values restored all 148 zero splits. The original API audit stays
unchanged; a separate reconciled audit records the export hash and each change.
This fixes report input locally; it does not repair the API or edit timecards.

In TCP: Reports → Period Reports → Complete Payroll. Choose the affected dates,
clear employee status exclusions, and Download → OpenXML. Keep the resulting XML
under a private reports folder. A report can cover the whole payroll period or a
subset of dates, but every worked shift in those dates must match the API.
Leave, comp time, open punches, missing shifts, duplicate shifts and conflicting
nonzero calculations cannot be silently substituted by this fallback.

With an API audit and same-period Gateway JSON already downloaded from the private
workflow artifact, no local secrets are needed to reconcile and attempt a preview:

```powershell
python 'tips & payroll/tcp_report_fallback.py' `
  'tips & payroll/reports/PERIOD/tcp-audit.json' `
  'tips & payroll/reports/PERIOD/Complete Payroll.xml' `
  'tips & payroll/reports/PERIOD/tcp-reconciled-audit.json' `
  --gateway-input 'tips & payroll/reports/PERIOD/gateway-input.json'
```

This command never sends email. It writes remaining corrections in `native-preview`
and produces workbooks/email preview only when the completed 14-day period passes
all TCP and Gateway checks. Live local runs also accept `payroll_run.py --tcp-export
PATH` (repeat for separate exports). The scheduled GitHub run has no browser XML
source; fully unattended delivery still requires the TCP API discrepancy to be
resolved. Do not enable the schedule merely because the fallback matched.

The September 6–19 fallback check now has zero closed zero-hour shifts. It still
blocks on three open punches, the unknown title, and missing historical worked
days September 6–7. Those two dates are outside the upcoming September 8–21 payroll.
September 8–21 cannot be finalized until September 22; obtain fresh sources after
the period closes and after any corrections.

Run tests: `python -m unittest discover -s "tips & payroll" -p "test_*.py" -v`.

## September 22 reconciliation

The completed September 8–21 period was reconciled against a fresh full-period
Complete Payroll OpenXML export. All 2,325 worked shift records matched exact
GEIDs, job codes and punch times. The export restored 280 zero API calculation
splits, with every existing nonzero split agreeing. Equal-punch zero-duration
markers are retained only when both sources match; positive-duration zero hours
still block payroll. Native employee headers may include an optional Export Code;
that field is not assumed to be a verified payroll-system ID.

Roster-only people with no worked shifts can now remain visible as UNASSIGNED,
at zero hours and tips, with setup warnings. A person without a mapped home store
appears in the combined roster. Any worked hours for an unassigned title still
block allocation and email. Managers remain excluded across all stores.

The dated Excel outputs and source audits remain private under `reports/`.
This completed manual reconciliation does not resolve the unattended TCP API
discrepancy. No ADP import is needed to prepare these reports.

Scheduled delivery is enabled at the owner's request. The first trigger is
08:00 America/Los_Angeles, with retries at 08:20, 09:20 and 11:20. The alternating
Tuesday gate is anchored to September 22, 2026 (next dates October 6, October 20,
November 3). The IANA time zone keeps 08:00 correct through daylight saving time.
GitHub may queue or drop scheduled triggers; this is a requested start time,
not a guaranteed email arrival time. Delivery receipts prevent duplicate email.
Incomplete TCP/Gateway data still fails the run instead of sending an incomplete
payroll. A fresh September 22 API check after generating the native report still
returned the same 280 zero calculation splits, so unattended completion remains
dependent on resolving the API discrepancy or supplying a matching native export.
