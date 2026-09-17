# Tips and payroll

Current task: allocate each store's Gateway tips among eligible crew using
verified decimal hours, then prepare the ADP payroll export. Store **3356-01**
is an example; the same calculation applies separately to every store.

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
- [Hours](https://timeclock-plus.readme.io/reference/hours_getall) provides
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
Accepted crew titles are `Crew` and `Crew Member`. `Crew Leader` needs an
explicit eligibility classification before it can enter a live calculation.

The supplied screenshot verifies the numeric rule: `766.36 / 583.41 * 59.51`
gives `78.17`. Automated tests reproduce all 15 example amounts and reconcile
their sum to `766.36`.

## Current source limitations

The Gateway artifact retrieved from `tips-yolo` covers August 25–September 7,
2026. The TCP/PDF comparison covers September 1–14, 2026. Do not combine them.
The API snapshot omitted calculated hours for September 14 even though the
payroll PDF included them; the incomplete snapshot must not feed an allocation.

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

These are recorded requirements, not an activated schedule. The current Action
only performs manual TCP inspection. Gateway orchestration, ADP credentials and
access, Google Workspace sender authorization, exports, and live scheduling
still need integration and end-to-end validation. Do not mark a period completed
or send a successful-payroll report when inputs or imports are incomplete.

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

The certificate wizard identifies the organization as `LC CO LLC`. It requires
the organization's country, state, and city; the city/state have been requested
from the user. No certificate was generated. The portal requires a signed ADP
certificate and private key along with the client ID/secret; its token endpoint
is `https://accounts.adp.com/auth/oauth/v2/token`.
The user will copy credentials themselves. Access to AZ and TX companies still
needs verification; creating this project does not establish cross-company access.

References:
- https://developers.adp.com/articles/preview/guide-worker-management-api-guide-for-adp-workforce-now-0
- https://developers.adp.com/articles/preview/guide-payroll-data-input-api--guide-for-adp-workforce-now-0?chapter=2
- https://apps.adp.com/en-US/apps/410612/ADP%C2%AE%20API%20Central%20for%20ADP%20Workforce%20Now%C2%AE/features
