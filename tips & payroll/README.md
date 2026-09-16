# Tips and payroll

Current task: identify store **3356-01** in TCP and calculate its **total
hours and total tips for the current pay period**.

## Setup

Copy `.env.example` to `.env` in this folder and enter the API key, bearer
token, and actual current pay-period dates. Keep the token free of the
`Bearer ` prefix; a future API client will add that prefix to the header.
No API client has been implemented yet and no company data has been retrieved.

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

## Remaining work

1. Supply local credentials and confirm the current pay-period boundaries.
2. Resolve the store's identifier and confirm how worked shifts map to stores.
3. Verify the tip field and meaning against company configuration or a known report.
4. Retrieve all relevant pages using read-only requests and calculate totals.
   Account for regular/overtime categories without double counting and disclose
   incomplete punches or unavailable tip values rather than treating them as zero.

Keep downloaded records in `data/` and output reports in `reports/`; both are
ignored by Git.
