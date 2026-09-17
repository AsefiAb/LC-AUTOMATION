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
