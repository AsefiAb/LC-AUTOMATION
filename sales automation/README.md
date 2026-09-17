# Daily TCP sales reporting

Workbook/email components and a read-only TCP labor adapter are implemented.
Full live delivery is blocked: the current TCP API has no confirmed sales,
transaction-count or actual labor-cost feed. No emails have been sent and no
existing workbook has been changed by this implementation.

## Confirmed requirements

- TCP is the **only** business-data source. No Altametrics or Gateway fallback.
- Run daily at 08:00 **America/Los_Angeles**, including daylight-saving changes.
  Report the previous Pacific calendar date.
- Keep the three existing CO, TX and AZ OneDrive workbooks. Their links, all
  18 stores, and the reference repo's labor goals are in `config.json`.
- Update `SALES` and `Trans` by date, preserving formulas and formatting.
- Attach all three updated workbooks and show store sales, transactions, decimal
  labor hours, actual labor percentage and goals in the email.
- From: **ab@caazllc.com**, through Google Workspace.
- To: ab@caazllc.com, alicia@lccollc.com, kerry@lccollc.com,
  carter@franbiznetwork.com, alex@caazllc.com.
- Cc: jeff@caazllc.com, caleb@lccollc.com, wilder@lccollc.com.

The original `AsefiAb/salesautomation` workflow has not been changed. Disable that
old daily workflow before enabling this replacement to avoid duplicate reports.

## TCP findings and the missing input

The published TCP v1.30.0 schema includes `/v1/hours` with `includeRate`,
`/v1/worksegments`, job codes and locations. No documented sales, revenue or
transaction-count endpoint was found. The account's 140 custom-field definitions
also contained no sales/transaction matches. Another TCP product or an
account-specific reporting service may offer these; the checked API does not
establish that availability.

The live read-only probe for **September 16, 2026** returned:

- 133 closed worked segments, with usable rates on 131.
- Store 3356-01: 47.68 decimal worked hours.
- Store 3356-05: an open punch.
- Store 3356-02: no worked segments, so zero labor cannot be assumed.

[Probe run](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35274081388).
Only store aggregates and counts are saved, not employee names or individual
rates. Artifacts are private and retained for three days.

Hours times returned rates are only a base-wage estimate. Overtime premiums,
salary allocations, taxes, benefits and business-day boundaries are unverified.
These estimates are never used as actual labor cost or percentage in the email.

Ask TCP support/account management:

> We need an authenticated read-only feed of daily store net sales, transaction
> counts and actual labor cost for our LCE locations. Our TCP OpenAPI documents
> hours/worksegments but no sales endpoint. Which API or reporting service exposes
> these values for our account? Please provide endpoint docs, required scopes,
> sample field definitions, store identifiers, business-day cutoff/timezone, and
> whether labor cost includes OT premiums and salaried staff.

The feed must match the existing metrics: Product Net and Actual Total Trans.
If TCP cannot supply these values, the full TCP-only sales report cannot run.

## Pipeline and verification

1. Check configuration, connections and the previous delivery receipt.
2. Read TCP hours/work segments with pagination. Reconcile IDs and flag missing
   stores, open punches, duplicate records or uncalculated shifts.
3. Read verified TCP daily metrics. Require exactly one row per expected store,
   matching dates, finite nonnegative values and whole transaction counts.
4. Download all three OneDrive files and validate all changes before uploading.
   Match each sheet's date in columns A-D and verify store headers in E onward.
   Missing/ambiguous dates or headers, formulas in input cells, and conflicting
   existing numbers stop the update. Never guess the next empty row.
5. Update only store input cells; preserve unrelated ZIP parts, charts, formulas
   and formatting. Excel recalculates formulas when opened; this job does not
   recalculate cached formula results.
6. Upload with an ETag check to prevent overwriting simultaneous edits. Re-read
   the files and verify changed inputs before sending email.
7. Send all three workbooks using Gmail SMTP and save a non-financial delivery
   receipt in the private repo's `sales-delivery-state` branch.

OneDrive cannot update three files atomically. A partial upload sends no email;
a retry recognizes already-matching cells and finishes the remaining files.
An SMTP timeout or partial-recipient rejection leaves a `sending` receipt and
blocks blind retries. Check Gmail Sent before manually resolving that receipt.
No payroll or workbook data is committed to the delivery-state branch.

## Required connections in LC-AUTOMATION

| Secret | Purpose | Status |
| --- | --- | --- |
| `TCP_CLIENT_ID`, `TCP_CLIENT_SECRET`, `TCP_API_KEY` | TCP access | Present |
| `CAAZ_GMAIL_APP_PASS` | Gmail app password for ab@caazllc.com | Present in old salesautomation repo; user must copy its value here |
| `MS_CLIENT_ID` | Microsoft OAuth application | Needed |
| `MS_REFRESH_TOKEN` | Delegated OneDrive file access | Needed |
| `MS_CLIENT_SECRET` | Confidential Microsoft app only | Optional; helper uses a public client |

GitHub cannot reveal secret values for copying between repositories. The old
Outlook password is not reused by this integration.

### OneDrive authorization

The reference uses personal OneDrive links. Microsoft's Excel workbook API
excludes OneDrive Consumer, so the code uses Graph file download/upload APIs and
updates the original XLSX package locally.

Register a Microsoft public-client app allowing the account that can edit the
three files, enable public-client flows, and allow delegated `Files.ReadWrite`
and `offline_access`. Run the helper locally with that app's ID:

```powershell
python -m pip install -r 'sales automation/requirements.txt'
python 'sales automation/connect_onedrive.py' --client-id YOUR_MICROSOFT_APP_ID
```

Complete Microsoft's device sign-in. Copy the two values in the ignored
`sales automation/data/onedrive-secrets.json` to GitHub Secrets, then remove that
local file. Tokens are not printed. This workspace is in OneDrive, so ignored
files may still be cloud-synced. Reauthorize if Microsoft revokes or expires the
refresh token; authentication failures stop the run.

## TCP metric adapter

`tcp_metrics` stays null until a real endpoint is provided. No endpoint has been
invented. The current adapter supports a TCP v1 paginated daily-store aggregate
resource configured with:

- `resource`: relative resource name from confirmed TCP documentation.
- `verified`: true only after dates and totals have been reconciled.
- `params`: documented filters; `{report_date}` is replaced with the date.
- `fields`: actual JSON field paths for `store`, `date`, `sales`, `transactions`
  and `labor_cost` (all required).
- Root `labor_cost_verified`: true only after checking the actual cost definition
  and business-day alignment against an authoritative report.

A different TCP reporting-service contract will need an adapter when its docs
are available. Do not set verification flags to bypass missing data.

## Run and activate

Actions -> **Daily TCP sales report** -> Run workflow:

- `preflight`: inspect configuration and TCP labor; no workbook writes or email.
- `preview`: prepare private updated workbook copies and email HTML from verified
  data; no OneDrive uploads or emails.
- `deliver`: update all three workbooks and send to the configured recipients.

Schedule activation requires repository variable `SALES_AUTOMATION_ENABLED=true`.
It remains unset until sources and connections work and a real preview is checked.
The workflow checks at 15:00/16:00 UTC and only runs at local 08:00. GitHub can
start late; exact 08:00 execution is not guaranteed. Delays beyond that hour are
skipped; use a manual report-date run to recover a missed report.

Before activation, verify the real workbook date/header layout, complete a private
preview and disable the original repo's daily workflow.

```powershell
python -m unittest discover -s 'sales automation' -p 'test_*.py' -v
python 'sales automation/daily_report.py' --date 2026-09-16 --preflight
```

The separate **Inspect TCP sales labor capability** Action needs only TCP secrets.

## References

- https://timeclock-plus.readme.io/reference/hours_getall
- https://timeclock-plus.readme.io/reference/worksegment_getworksegments-1
- https://learn.microsoft.com/en-us/graph/api/resources/excel?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/api/driveitem-put-content?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code
- https://support.google.com/a/answer/176600
