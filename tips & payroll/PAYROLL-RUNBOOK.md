# Payroll reporting without ADP

## Current behavior (October 6, 2026)

The scheduled job requests an 08:00 Pacific start on alternate Tuesdays. GitHub
can delay scheduled jobs, so UTC catch-up triggers also run at 07 and 37 minutes
past 15:00–18:00 UTC. The Pacific date/hour gate rejects the wrong week and any
attempt before 08:00. An exact email arrival time is not guaranteed.

Missing TCP calculations, failed Gateway access, or old/missing ADP roster exports
now produce three regional **INCOMPLETE PAYROLL REVIEW** emails when delivery is
enabled, with regional and per-store review Excel attachments. Unknown amounts
remain blank. Store tip pools are allocated using the existing exact-cent formula
only when that store has complete TCP hours and known eligible roles. A known
employee punch/role problem holds the tip pool at every store where that person
worked, because missing time can also change cross-store overtime. Unknown or
global source failures hold every pool. Held pools are labeled unallocated, never
zero; clean stores continue to show calculated tips even when ADP exports are old.
These packages contain **no ADP CSV** and must not be imported or treated as final
payroll. Issues are included in the workbooks. The workflow remains failed with
exit code 2 until reconciliation is complete; successful email transport alone
does not make payroll correct.

Review emails have separate per-region/per-period receipts. Retries do not repeat
the same review email, and a review receipt does not prevent a later corrected
payroll package. Check `review-delivery-results.json` and `REVIEW ONLY/` in the
private artifact. Preflight remains read-only and sends nothing. Preview generates
the same review files without sending. Current ADP exports are still required
each period before native import CSVs can be produced.

On October 6 the owner confirmed employees clock directly in TCP and authorized
TCP as the payroll hours source for **September 22–October 5 and future periods**.
`source_reconciliation.source_policy` records this effective-date rule. Altametrics
comparisons remain informational for these periods, without merging or replacing
TCP hours. **September 8–21 and earlier periods retain independent reconciliation**.
This changes only the hours-source choice: open punches, missing calculated shifts,
role conflicts, manager exclusion, store-only tip pools, and current ADP identity
controls remain enforced. Missing ADP exports block import CSVs, not useful Excel
review reports. `review-readiness.json` records each allocated or held store pool.

Known unresolved job-title conflicts are retained in the private GitHub secret
`PAYROLL_ROLE_REVIEWS`, with schema
`{"version":1,"unresolved":[{"geid":"...","source_role":"co-manager","evidence_ref":"private report reference"}]}`.
These reviews block tip allocation and final imports until the correct employee,
role and effective dates have been confirmed. They do not change TCP's recorded
role. A later title change does not silently close a review. After documented
resolution, remove that entry; an explicit `{"version":1,"unresolved":[]}` means
all known reviews have been closed. Missing/invalid configuration blocks readiness.
The registry applies to scheduled runs and reviewed native-export packages.

For an owner-confirmed management exclusion covering one exact payroll period,
retain the employee's `unresolved` entry and add a `confirmed` entry:
`{"geid":"...","role":"co-manager","period":{"start_date":"2026-09-22","end_date":"2026-10-05"},"evidence_ref":"Owner confirmation reference"}`.
Only recognized management roles are accepted. For that exact period, the
normalized payroll role changes to the confirmed management role, source roles
remain under `tcp_roles`, and the person receives zero tips. No TCP record is
edited. Other periods retain the open review rather than assuming the historical
confirmation continues forever. Duplicate/overlapping or malformed confirmations
block readiness. Remove the open review only after its broader effective dates
and TCP role setup have been resolved.

The historical implementation notes below describe earlier behavior. This section
supersedes the earlier silent delivery holds for incomplete input.

GitHub Actions **Biweekly payroll reports** connects Gateway Financial View tips
and TCP calculated hours. The pipeline writes 18 store workbooks and three
separate regional totals workbooks, each containing one worksheet. Alicia gets
three emails, each with its regional totals and matching store files. Each body
contains a short greeting, period, and regional regular hours, overtime hours,
total hours and tips. Employee details stay in the attached Excel files. It does
not access or import into ADP.

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
- Regional sheets list each person once per region, with their regional regular,
  overtime and tip totals and no store-number column. Existing store allocations
  are summed; tips are never redistributed across stores or regions.
- Include the current TCP roster and people with worked hours during the period.
  People with no worked shifts and no role remain UNASSIGNED at zero hours/tips,
  with a setup warning; without a home store they remain in the private combined
  roster and email setup notes instead of being assigned to an arbitrary region.
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

From **ab@caazllc.com**, to **alicia@lccollc.com**. Each email gives only the short
regional summary. The attached regional Excel file combines every person's hours
and tips across stores within the region, including managers. Each person counts
once. A brief note is included only while a zero-hour, unassigned person has no
home store and therefore cannot appear in a regional file.

## Run and activate

1. Use `preflight` to check live TCP/Gateway without email. Blank dates choose the
   latest completed scheduled period; manual dates must be a completed 14 days.
2. Use `preview` to create all workbooks and three plain-text email files privately, without sending.
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

## Regional email delivery

### Current ADP import layout (September 22 redesign)

Owner update: send reports even when ADP identity matches remain unresolved.
Attach a separate regional `Payroll issues <period>.xlsx` with every unmatched
person's complete regular hours, overtime, total hours and tips. Verify that the
ADP CSV plus this issues sheet equals the full verified regional payroll before
sending. Keep the ADP filename unchanged, but explicitly state in the email that
it is incomplete until the issues are corrected. The email and store-sheet totals
always include the unresolved employees. Do not invent file numbers or silently
discard pay. A successful report delivery does not imply ADP import readiness.
TCP/Gateway calculation errors and unexplained reconciliation differences still
block delivery. Existing regional receipts prevent duplicate TX/AZ email when CO
is subsequently sent with an issues attachment. This update supersedes the
identity-mismatch delivery hold described in earlier implementation notes below.

The regional attachment is now the native ADP CSV, not the former regional XLSX:
CO `EPIFND00.CSV` (FND), AZ `EPIFNC00.CSV` (FNC), TX `EPIZKT00.CSV` (ZKT).
Keep these filenames unchanged. Preserve every control line, employee file number,
ADP name, rate, company, batch, and native last-name sort order. Change only regular
hours, overtime hours and tips. Texas uses earnings code `T` and the heading
`Other Earnings - TIP EARNS`; CO/AZ use `TIP`. Never substitute one for the other.

`report_package.py` recomputes the store pools from Gateway and TCP, sums each GEID
once per region across its stores, then matches the current ADP export. Unique
exact names and alternate names explicitly provided in ADP parentheses can match.
Spelling differences and duplicate names need owner-confirmed mappings with both
source names and the ADP file number. No fuzzy match can assign payroll.

Owner instruction: zero-hour TCP people absent from ADP stay out of imports and
appear in `Add to ADP or review for termination.csv`. This is a review list, not
an instruction to terminate anyone. Unmatched people with hours or tips block
delivery; their earnings must never disappear. ADP-only rows remain present at
zero with a warning in the private reconciliation audit.

Each store workbook has one sheet with GEID, employee name, regular hours, overtime,
total hours, and tips, sorted by last name. The top shows Gateway tips, eligible
crew hours, and tips per hour. Manager exclusion and penny reconciliation remain
unchanged. Local authoring uses the bundled artifact runtime; GitHub-hosted runners
use openpyxl because the desktop artifact runtime is unavailable there.

For reviewed source runs, dispatch **Build ADP imports and store payroll reports**.
Its seven-file encrypted package contains the three source files (`tcp-audit.json`,
`native-payroll.xml`, `gateway-input.json`), the three current ADP CSVs, and
`settings.json` with period, `allow_missing_zero`, and confirmed `identities`.
Preview runs create private artifacts without emailing. Import mismatches fail the
run and keep incomplete CSVs inside `NEEDS REVIEW`. An explicitly dispatched
delivery sends only regions that completely reconcile; blocked regions are held
and the overall run remains failed/partial. Per-region delivery receipts show
exactly which emails were sent. Select `refresh_sources` to fetch TCP and Gateway
again and reconcile them with the native report before building any attachments.
Replacements use a separate `adp-format-v2` receipt and a
short replacement notice; ordinary retries still cannot duplicate emails.

Scheduled runs use base64 ADP templates from `ADP_CO_TEMPLATE`, `ADP_AZ_TEMPLATE`,
`ADP_TX_TEMPLATE` and JSON `ADP_ROSTER_SETTINGS` in GitHub Secrets. Refresh these
from current ADP exports for each period; period mismatches block old rosters.
Without an ADP roster API connection, new file numbers and updated ADP rates must
come from those exports. Existing TCP zero-calculation failures still require
native-export reconciliation. Scheduling alone cannot fill missing source data.

The paragraphs below describe the earlier XLSX regional layout retained only
for historical package validation; scheduled delivery uses the current CSV layout.

Each completed period produces three separate emails to alicia@lccollc.com from
ab@caazllc.com: `TX TIPS <period>`, `AZ TIPS <period>`, and `CO TIPS <period>`.
For September 8–21 the period label is `9.8.26-9.21.26`; dates are derived from
the validated period on every run. Each email attaches one single-worksheet
regional totals Excel file plus that region's individual store workbooks
(TX: 4 stores, AZ: 7, CO: 7). Regional rows combine a person's hours and previously
allocated store tips by GEID, without store columns or redistributing tip pools.

The delivery ledger keeps a separate receipt for each region and period. Confirmed
sends are skipped when retried; uncertain SMTP outcomes require checking Gmail Sent.
An existing legacy combined-email receipt blocks new regional delivery for that
period until reviewed, avoiding a duplicate payroll email.

For a reviewed native-export fallback, stage `tcp-audit.json`, `native-payroll.xml`,
`gateway-input.json`, and the 21 reviewed Excel files in
`%LOCALAPPDATA%/LC-Automation/payroll-delivery/START_END` locally.
`send_reviewed.py --start START --end END` verifies the sources and every
employee's cached Excel values without sending. The manually dispatched
**Send reviewed regional payroll** workflow repeats those checks and sends the
three messages on a GitHub-hosted Ubuntu runner using the existing Gmail secret.
Upload the 24-file ZIP encrypted with PyNaCl SecretBox as a private draft-release
asset; save its random 32-byte base64 key in `PAYROLL_REVIEWED_PACKAGE_KEY`.
Dispatch with the asset ID and SHA256 of the approved ciphertext. The workflow
checks the digest, decrypts into temporary storage, reconciles both sources, and
rechecks every employee and attachment before sending. It deletes the decrypted
runner files afterward and shares the scheduled payroll concurrency lock.
Remove the temporary draft-release asset and transfer key after confirmed delivery.
No self-hosted runner or open browser is needed. Private source files and email
previews are never committed to Git. This is a reviewed fallback, not a fix for
incomplete future TCP API calculations.
