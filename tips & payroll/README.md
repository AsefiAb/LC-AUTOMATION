# Tips and payroll

ADP uploads are **CSV only**: Texas `EPIZKT00.CSV` (company ZKT), Arizona
`EPIFNC00.CSV` (FNC), and Colorado `EPIFND00.CSV` (FND). Store and issues
workbooks remain `.xlsx` supporting reports. Never rename an Excel workbook
to `.CSV`; the generator writes native CSV and verifies its filename, contents,
control rows, employee identities, and decimal amounts before attaching it.
Each run includes `ADP import checks.txt` with the period and CSV batch totals.
Compare those totals and employee file numbers with ADP's import results, and
review rejected rows before finalizing payroll. An issues attachment means the
CSV excludes unresolved employees; the store reports still include them.

An explicitly reviewed API-only shift can be listed separately using a
period-specific `approved_work_exceptions` entry in the private roster settings.
The record ID, employee ID, job, both punches and store must still match exactly,
and the record must remain absent from the roster and calculated/native payroll.
Unknown hours and tips are labelled unverified, never assigned zero. The regional
issues workbook and email disclose the exclusion and provisional store tips.
Any new or changed mismatch still stops the run. The manual replacement workflow
uses the `adp-format-v3` delivery receipts to send the requested revised reports
once, without duplicating them on retries.

The reporting pipeline is `payroll_run.py`. See [PAYROLL-RUNBOOK.md](PAYROLL-RUNBOOK.md) for its workflow, secrets, schedule, and current limitations. [Regional ADP CSV preparation](ADP-EXPORT.md) is implemented in `adp_export.py`; the live ADP connection and automated imports remain unfinished.

Allocate each store's Gateway tips among eligible crew and Crew Leaders using
verified decimal hours, then prepare dated store and consolidated workbooks. Store **3356-01**
is an example; the same calculation applies separately to every store.

The tip-collection reference is [AsefiAb/tips-yolo](https://github.com/AsefiAb/tips-yolo).
`gateway.py` ports its Gateway **Financial View** method: read each store's
**InStore Tips (+)** and **Prepaid InStore Tips (+)** totals and add them together.
The reviewed source is `tips_bot.py` at revision
`f28a2698e41dee97df2e673032e34fe06450983d`. Its 7 Colorado, 7 Arizona, and 4 Texas
stores match this pipeline. Each new Gateway audit records that reference and
both source fields. TCP supplies employee hours and job titles; Gateway supplies
the tip amounts. The payroll adapter uses the requested pay-period dates and
stops on incomplete data. It does not substitute an old repository artifact with
different dates or run the original repository's separate Altametrics code.

## Setup

Copy `.env.example` to `.env` in this folder and enter `TCP_CLIENT_ID`,
`TCP_CLIENT_SECRET`, the separate `TCP_API_KEY`, and actual current pay-period
dates. The client ID and secret are exchanged for an access token; they are
not interchangeable with the API key. A manually supplied bearer token is
not needed for this setup.
The read-only inspection client is `tcp_inspect.py`. It uses environment
variables; it does not load `.env` automatically.

## Run using GitHub secrets

The private repository's **Inspect TCP payroll data** Action uses the repository
secrets `TCP_CLIENT_ID`, `TCP_CLIENT_SECRET`, and `TCP_API_KEY`. The optional
repository variable `TCP_COMPANY_ID` selects a non-default company.

Run the workflow manually with start and end dates to inspect hours and work
segments as well as stores, job codes, and employees. Omit both dates for catalog
discovery only. The report is a private `tcp-inspection` workflow artifact retained
for three days. Credentials are never included. Personal contact information,
birth dates, and pay rates are excluded from the output. No TCP records are changed.
Reports are not committed to the repository.

This is an inspection report, not a finalized payroll calculation. Tip fields,
store attribution, and role mappings must be verified against returned data.

## Authentication

TCP's [authentication guide](https://timeclock-plus.readme.io/docs/authentication)
documents a form-urlencoded POST to
`https://auth.api.tcplusondemand.com/oauth2/token` with `grant_type=client_credentials`,
`client_id`, `client_secret`, and `scope`. The template requests
`tcp-openapi/tcpopenapi.read` for this reporting integration; the guide's example
requests both read and write scopes. Read-only token issuance has been verified.
Tokens expire after one hour and should be generated when needed.

Data requests require the separate `x-api-key` header and
`Authorization: Bearer <access_token>`. `X-Tcp-CompanyId` is optional and defaults
to `1`; set `TCP_COMPANY_ID` if the intended company differs. If TCP supplied
only a client ID and secret, request the separate API key from TCP or your
administrator.

## Documentation findings

- API base URL: `https://api.tcplusondemand.com`.
- Authentication requires `x-api-key` and `Authorization: Bearer <token>`.
- [Hours](https://timeclock-plus.readme.io/reference/hours_getall-1) provides
  calculated hours with date filters and pagination.
- [Locations](https://timeclock-plus.readme.io/reference/locations_getlocations-1)
  and cost codes can help establish how this company represents store 3356-01.
- [Work segments](https://timeclock-plus.readme.io/reference/worksegment_getworksegments-1)
  expose `tracked1`, `tracked2`, `tracked3`, and custom fields. The documentation
  does not establish which field, if any, this company uses for tips.
- The originally supplied accrual-bank endpoint creates accrual banks and is
  unrelated to reading hours and tips.

## Reporting rules

`summarize_inspection.py <inspection.json> <summary.json>` creates store,
employee/store, and shift summaries from an inspection artifact. Source and
output paths should be under the ignored `reports/` directory.

- Dates are inclusive and select shifts by clock-in date. Overnight shifts stay
  with the date on which they began. The hours request uses a one-day buffer,
  then filters shifts locally; buffered API period totals are not report totals.
- Work-segment queries use explicit timestamps and `includeSegmentTime=timeIn`
  so the final day's overnight shifts are included.
- Store attribution and worked roles use job-code descriptions and locations.
  Worked roles may differ from a person's permanent employment title.
- Sum TCP's calculated regular, overtime 1, and overtime 2 durations in minutes.
  Convert to decimal hours only for presentation. Exclude open punches and leave
  from worked-hour totals and retain them in the source detail.
- Reconcile calculated shift IDs with work-segment IDs and reject duplicate
  calculated segments. Missing monetary fields are not treated as zero tips.
- Flag closed shifts with elapsed time but zero calculated hours. These make
  the sample incomplete; do not infer overtime or replace them with raw punch
  durations without verifying TCP's calculation and break rules.

## Confirmed tip allocation rules

The user specified the following rules and confirmed that the attached ADP
CSV was only a format example, not the live payroll roster or batch.

- Show and use decimal hours to two decimal places, such as `22.13`.
  User-facing allocation and payroll exports must not use hours:minutes.
- Use each store's Gateway total: InStore Tips plus Prepaid InStore Tips.
- Allocate across the whole selected period, separately for each store:
  `employee tips = store tips / total eligible crew hours * employee crew hours`.
- Only crew receive tips. Exclude managers, co-managers, and assistant managers
  from both payouts and the hours denominator. If an employee appears in a
  management role anywhere in the input period, exclude that person throughout
  the allocation. Do not infer crew eligibility from an unclassified title.
- Regular and overtime worked hours count equally, with no overtime multiplier.
  Sum the employee's worked hours at each store before rounding that total to
  two decimal places. Employees working at several stores receive a separate
  share from each eligible store pool.
- Preserve the unrounded allocation rate. Round payouts to cents, distributing
  leftover pennies by largest fractional remainder (employee ID breaks ties),
  so payouts add up exactly to the store's Gateway amount.
- Require matching dates and verified sources. Incomplete API hours, open
  punches affecting totals, unclassified roles, or missing store totals must
  be resolved before producing a payroll-ready allocation.

`allocate_tips.py <gateway.json> <payroll.json> <output.json>` implements this
calculation. Store private input and output files under `reports/` or `data/`.
Both inputs require `verified: true`, no unresolved `issues`, and identical
`period` objects with `start_date` and `end_date` in `YYYY-MM-DD` format.

Gateway input has `stores` containing `store` and `total_tips`. Payroll input
has `employees` containing `store`, `employee_id`, `name`, `roles` (a list), and
`total_hours` (decimal hours to two places, including overtime).
Accepted eligible titles are `Crew`, `Crew Member`, and `Crew Leader`, as confirmed
by the owner. Management titles remain excluded.

The supplied screenshot verifies the numeric rule: `766.36 / 583.41 * 59.51`
gives `78.17`. Automated tests reproduce all 15 example amounts and reconcile
their sum to `766.36`.

## Current source limitations

September 20 update: a native TCP Complete Payroll XML export matched all 148
September 14 shifts exactly and supplied their missing calculated regular/OT
splits. The verified manual fallback is described in [the runbook](PAYROLL-RUNBOOK.md#verified-manual-fallback-for-the-api-discrepancy).
It preserves the original API evidence and all remaining delivery checks. This
does not yet make the scheduled API-only workflow ready for unattended payroll.

The Gateway artifact retrieved from `tips-yolo` covers August 25–September 7,
2026. The TCP/PDF comparison covers September 1–14, 2026. Do not combine them.
The API snapshot omitted calculated hours for September 14 even though the
payroll PDF included them; the incomplete snapshot must not feed an allocation.
All 148 closed zero-hour segments in the September 6-19 diagnostic fall on
September 14. These are API discrepancies to investigate, not instructions to
re-enter or change the punches. The supplied September 1-14 PDF provides
independent evidence of nonzero hours on that date. Review the open punches and
missing job assignment separately; use the original PDF/API segment IDs when
raising the calculated-hours issue with TCP support.
The September 20 read-only Manager review confirmed the same shift's nonzero
regular/overtime hours in the live UI. A fresh API request aligned to September
8-21 reproduced its zero-hour result, ruling out the earlier date-range mismatch
for that sample. All three flagged prior-day open punches remain Clocked In;
Maximo's Jobs section lists only Sick, including inactive assignments. No source
records were changed. Private review notes and an unsent TCP support draft are
under `reports/tcp-browser-review-2026-09-20/`.

The sample ADP CSV is a formatting reference only. Do not assume its company
code, batch ID, employee roster, rates, or existing values are the actual target
payroll. Live export still requires the correct company/file-number mapping
and an aligned, verified data set.

Keep downloaded records in `data/` and output reports in `reports/`; both are
ignored by Git.

## Confirmed regional payroll workflow

The payroll product is ADP Workforce Now. Refresh each region's complete payroll
roster for every period; never reuse an example CSV's employee list as a fixed
roster. Include every employee in the regional export, including management and
employees without worked hours. Also reconcile everyone with TCP hours during
the period, including employees who have since left. An employee with TCP hours
but no verified ADP identity needs resolution before that region is imported.
Missing TCP records are not evidence of zero hours until completeness is verified.

Produce separate exports for CO, AZ, and TX, and import each into its verified
ADP company/payroll group and period. An ADP company code and payroll file number
identify the target; names assist initial matching but must not be the sole key
for unattended imports. Use an established TCP GEID-to-ADP identity mapping and
flag ambiguous or unmatched names. New employees must be included when mapped;
do not create ADP employee profiles as a side effect of exporting payroll.

The user identified `EPIFND00.CSV` as the Colorado example. Inspection found 193
employee rows, all with company code `FND`, with no duplicate company/file-number
pairs. Its example batch is `Wrksh001`; select the actual batch for each run.
The nine columns are `!CO CODE`, `BATCH ID`, `FILE #`, `BATCH DESCRIPTION`, `Name`,
`Rate`, `Reg Hours`, `O/T Hours`, and `TIP`. Preserve the ADP header and mapping
rows, payroll identifiers, and current ADP rates. Replace period-specific hours,
overtime, and tip values with verified results; never carry old amounts forward.
The TIP metadata maps Earnings 3 Code and Earnings 3 Amount to the `TIP` code.
Confirm that earnings code against each company's live ADP metadata.

Create one workbook per store, named for example
`3356-01 9.8.26-9.21.26.xlsx`, with crew followed by management. Columns:
GEID, Payroll ID, First Name, Last Name, Job Title, Total Hours, Tips, Reg Hours,
OT Hours. Management tips are zero. Keep the store's full-precision tip rate in
a separately labeled field, and show separate crew and all-employee hour totals.
The same verified data must feed the workbooks, regional CSVs, and API batches.
Using the API to create a batch does not require uploading the CSV itself.

## Combined payroll summary for Alicia

Before delivery, read the final dated store Excel sheets and combine people by
their verified **GEID**, never by name alone. Each person appears once in the
all-employee summary, even when they worked at multiple stores. Include the
complete current-period roster, including employees and managers with no worked
hours after completeness has been verified.

`payroll_email_summary.py` now prepares:

- A managers-only list with combined regular hours, overtime hours and total hours
  across stores. Managers, co-managers and assistant managers have zero tips.
- A complete employee-and-manager list with GEID, payroll identifiers, name, job
  title, stores worked, regular hours, overtime hours, total hours and tips.
- Alicia's email with both lists in its body, the dated store XLSX attachments,
  and Excel-readable CSV attachments for both combined lists. The managers are
  included in the full list and counted once in the overall totals.

All hours display as decimal hours with two places. Crew tips are the sum of
their finalized per-store allocations. The combined list does not reallocate
tips, apply an overtime multiplier to hours, or recalculate overtime using an
80-hour two-week threshold. TCP's regular/OT classification must already account
for the applicable cross-store work. OT1/OT2 detail remains in the payroll source;
the email's OT column combines overtime hours for display only.

Regional ADP exports remain separate and retain company/file-number identity.
These summary CSVs are for review and are not ADP import files.

### Preparing the email

Copy `payroll-email-manifest.example.json` into a private period folder under
`reports/`. Populate the complete verified roster, every expected store, its
matching Gateway tip total, and the exact workbook/sheet/header-row references.
The example is deliberately unverified and incomplete; do not use its example
store list or zero tip amount as live input.

The workbook layout uses `GEID`, `Payroll ID`, `First Name`, `Last Name`,
`Job Title`, `Total Hours`, `Tips`, `Reg Hours`, and `OT Hours`. Store workbook
filenames must match the manifest period, such as
`3356-01 9.8.26-9.21.26.xlsx`. Each store has exactly one workbook, with one
period-total row per GEID. `Total Hours` must equal `Reg Hours` + `OT Hours` in
the displayed two-decimal units. Recalculate and verify any Excel formulas
before preparing the manifest; missing cached results and Excel errors block
the summary instead of becoming zeros.

The manifest records `verified`, `roster_verified`, `cross_store_ot_verified`
and unresolved `issues`. These represent completed source checks, not an
automatic verification of live TCP/ADP access. Roster roles must describe the
selected period. Unknown roles, duplicate employee/store rows, unknown GEIDs,
name/ID conflicts, manager tips, missing stores and unmatched Gateway totals
stop preparation. Source hashes ensure the attached workbooks still match
the files used to calculate the email.

```powershell
python -m pip install -r 'tips & payroll/requirements.txt'
python 'tips & payroll/payroll_email_summary.py' 'tips & payroll/reports/PERIOD/manifest.json' 'tips & payroll/reports/PERIOD/email'
```

Outputs are `payroll-email.eml`, an HTML preview, combined CSVs, and an audit JSON
with source hashes and store/employee detail. The message is addressed from
`ab@caazllc.com` to `alicia@lccollc.com`. This stage **prepares** the email; it does
not send it, schedule delivery or import ADP payroll. The live payroll orchestration
must use this prepared message before its send/import stages.

## Confirmed timing and delivery

- First planned run: September 22, 2026 at 08:00 America/Los_Angeles.
- Repeat every 14 days, anchored to that date, not odd/even ISO week numbers.
- Interpret the user's "8am PST" as Pacific local time, adjusting for daylight
  saving time. This interpretation was stated to the user.
- September 22 covers September 8-21 inclusive; October 6 covers September
  22-October 5 inclusive; October 20 covers October 6-19 inclusive.
- The previous period was August 25-September 7, 2026 inclusive.
- Email the dated store documents from `ab@caazllc.com` (Google Workspace) to
  `alicia@lccollc.com`, then import verified regional pay-data batches.
- Keep durable period/company import receipts and email delivery records so
  retries do not duplicate batches or resend completed deliveries.

The biweekly workflow and delivery orchestration are implemented, but scheduled
delivery remains disabled pending reconciliation. Since September 22, a due
scheduled job runs read-only preflight checks even when
`PAYROLL_AUTOMATION_ENABLED` is unset or false, so blockers appear as failed
checks instead of an entirely skipped job. Only setting that variable to `true`
enables scheduled email delivery; manual preview never sends email. The September 20 live check
confirmed Gmail authentication and extracted Gateway tips for all 18 stores
for September 6-19 inclusive. The private artifact contains `gateway-input.json`.
[Live source check](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35539807831)
completed Gateway extraction; the overall run correctly failed the TCP checks.
TCP returned three open punches, 148 closed zero-hour shifts, missing calculated
work on September 6, 7 and 14, and no verified job title for GEID 6584508 in the
September 6-19 diagnostic period. The private artifact includes a corrections
CSV. Do not send a completed payroll report while these checks fail. ADP work
is on hold at the user's request.

As of September 17, GitHub contains `CAAZ_GMAIL_APP_PASS`, `ADP_CERTIFICATE` and
`ADP_PRIVATE_KEY`. `ADP_CLIENT_ID` and `ADP_CLIENT_SECRET` are still missing.
ADP connectivity is unverified. Gmail authentication and the combined Excel/email
summary are verified separately; a reconciled live-period payroll delivery has
not yet been performed.

## ADP permissions to configure

In ADP API Central, the Payroll Input use case is the relevant starting point.
Verify access to the following capabilities for each payroll company:

- Worker Management read: `GET /hr/v2/workers`, with pagination, for names,
  assignments, company codes, and payroll identifiers.
- Payroll Data Input metadata:
  `GET /events/payroll/v1/pay-data-input.modify/meta`, including company/file
  filters, to validate configured earnings codes and accepted input fields.
- Payroll Data Input write:
  `POST /events/payroll/v1/pay-data-input.modify`, to create the pay-data batch.

ADP documents the pay-data-input scope as
`/payroll/payrollManagement/payrollProcessing/payDataInputManagement/pay-data-input.modify`.
Store credentials only in GitHub Secrets; never commit credential values or
payroll records.

### ADP portal setup status (September 16, 2026)

Created the user's `Payroll` project with Payroll Input (Read/Write). Its initial
Allowed APIs list was empty. Added the following API bundles through discovery;
ADP displayed "6 APIs were successfully added to your project":

- `GET /hr/v2/workers`
- `GET /hr/v2/workers/{aoid}`
- `GET /hr/v2/workers/meta`
- `GET /hr/v2/workers/{aoid}/meta`
- `GET /events/payroll/v1/pay-data-input.modify/meta`
- `POST /events/payroll/v1/pay-data-input.modify`

The portal still showed an empty Allowed APIs list and "error retrieving client
credentials", including after a refresh. The client ID/secret copy controls were
disabled. The success notification confirms the add operation, but usable access
and credentials remain unverified. Do not re-create duplicate projects to retry.

The certificate wizard identifies the organization as `LC CO LLC`. The user
subsequently generated the certificate and stored `ADP_CERTIFICATE` and
`ADP_PRIVATE_KEY` in GitHub. Their values were not read and token access remains
unverified. ADP requires these alongside the client ID/secret; its token endpoint
is `https://accounts.adp.com/auth/oauth/v2/token`.
The user will copy credentials themselves. Access to AZ and TX companies still
needs verification; creating this project does not establish cross-company access.

References:
- https://developers.adp.com/articles/preview/guide-worker-management-api-guide-for-adp-workforce-now-0
- https://developers.adp.com/articles/preview/guide-payroll-data-input-api--guide-for-adp-workforce-now-0?chapter=2
- https://apps.adp.com/en-US/apps/410612/ADP%C2%AE%20API%20Central%20for%20ADP%20Workforce%20Now%C2%AE/features
