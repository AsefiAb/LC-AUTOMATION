# Tips and payroll

Current task: identify store **3356-01** in TCP and calculate its **total
hours and total tips for the current pay period**.

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
requests both read and write scopes. Read-only token issuance has not yet been tested.
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

The connection through GitHub secrets was verified. The company defines a
`Tipping` job code using tracked field 1, but no tip entries or tracked amounts
were returned in the September 1–14, 2026 sample. Confirm the tip source and
posting process before implementing tip totals or allocation.

Keep downloaded records in `data/` and output reports in `reports/`; both are
ignored by Git.
