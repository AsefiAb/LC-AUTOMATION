# Daily sales reporting

Daily sales delivery is enabled in **AsefiAb/LC-AUTOMATION**. The original
**AsefiAb/salesautomation** daily workflow is disabled; keep that repo for history.

**Altametrics** supplies Product Net sales and Actual Total transactions.
**TCP** supplies calculated worked hours and the returned wage rate for each
shift. The daily GitHub workflow combines these sources for the same date and
store, and displays `Labor 29.28% | Goal 23.50%` on each complete store line.
One source note below the stores explains the TCP wage calculation and its limits.

The formula is `(regular wages + overtime wages) / net sales x 100`.
It includes crew and management. All 18 stores' Altametrics settings were
checked on September 24: the business day starts at **4 a.m. store local time**,
OT1 pays **1.5 times** the rate, and daily salary/accrual additions are zero.
Whole shifts follow their start time; the cutoff never deletes worked hours.
TCP supplies the calculated regular/OT split; this adapter does not recalculate
payroll overtime. Each segment's regular and overtime costs are rounded to cents.

This is calculated labor based on TCP's recorded hours and rates, not finalized
payroll expense. September 24 percentages were reconciled against TCP's native
Estimated Wages report: all 17 numeric percentages matched, and the zero-sales
store remained N/A. TCP punches, rates and rounding can differ from Altametrics,
and later timecard corrections can change results. Taxes and benefits are excluded.
An unverified OT2 multiplier or
nonzero salary/accrual allocation blocks the affected store pending reconciliation.
See [LABOR_VERIFICATION.md](LABOR_VERIFICATION.md) for the source figures and limits.
Payroll exports and tip calculations are unaffected.

Open punches, zero-hour closed shifts, missing rates or calculated hours, and
stores without shifts display a specific `Labor unavailable (...)` reason.
Partial store wage totals are never published as a percentage. Unreconciled
segment IDs or unmapped stores invalidate all labor estimates. An unavailable
labor estimate does not prevent valid sales/transactions from being sent.
Zero sales displays `N/A (zero sales)`; failed TCP access never falls back to
Altametrics zero or stale labor. The original Altametrics percentage is retained
in `store-totals.json`; `tcp-labor.json` records per-store completeness and issues
without individual wage rates. Both are kept in the private run artifact.

`config.json` selects `labor_source: TCP wage estimate` and loads the verified
store settings from `labor_settings.json`. GitHub needs the
existing TCP_CLIENT_ID, TCP_CLIENT_SECRET and TCP_API_KEY secrets in addition
to Altametrics, OneDrive and Gmail credentials. Use a `preview` run to inspect
the email and all three workbooks without uploads or sends. A `preflight` run
reports any incomplete labor stores even though delivery can label them and
send the remaining sales data.

To recheck settings after a store configuration changes, run GitHub Actions ->
**Inspect Altametrics labor settings**. Review its private JSON artifact before
updating `labor_settings.json`; the audit only reads settings and sends no email.

## Verified September 23, 2026

- All 18 stores passed two complete reads through their dedicated local browsers.
- The [GitHub preview](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35926036136)
  prepared all three Excel attachments and the plain-text email successfully.
- The [live delivery](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35926455778)
  sent the September 22 report; its delivery receipt confirms SMTP acceptance
  with no rejected recipients at 22:09:54 UTC on September 23.
- Every attached sales/transaction cell matched the source. The existing OneDrive
  figures already matched, so this run did not need to overwrite those cells.
- All 45 sales tests passed in GitHub. Tomorrow's September 23 workbook rows
  were also verified. This does not guarantee a session will never expire.

The old September 23 run displayed success but logged a Gmail authentication
failure. It did not send the September 22 email. The new live run delivered it.

## Cloud-only execution

The daily workflow now runs on GitHub-hosted Ubuntu with a headless browser.
The owner computer, local runner and open Chrome windows are no longer needed.
Its local Windows startup entry has been removed. The original sales repo stays
paused to prevent duplicate reports.

`ALTAMETRICS_AUTH_MODE=cloud-legacy` uses the original repo's dashboard overlay
handling and SSO form submission, with bounded retries for stalled redirects.
It never changes passwords or substitutes today's figures for a failed date.
Credentials use COLORADO_USERNAME/COLORADO_PASSWORD, AZ_USERNAME/AZ_PASSWORD,
and TX_USERNAME/TX_PASSWORD. The local browser helpers remain diagnostic tools
only and are not called by the workflow.

The [cloud preview on September 23](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35929515395)
passed: all 18 stores, all three workbooks and the email preview for September 22.
The bounded SSO retry was exercised successfully for Colorado. Daily live delivery
remains enabled, and the existing receipt prevents resending September 22.

## Excel and email

The same three existing OneDrive workbooks are used. The updater matches the
report date and each store's columns in SALES and Trans, preserving formulas,
formatting, charts, and other workbook contents. Sales are rounded to whole
dollars in Excel, matching the old automation; email figures retain full cents.
Transactions remain whole counts.

Every store is required. Missing or ambiguous metrics, wrong dates, conflicting
existing cells, or unexpected workbook layouts stop delivery. All three files
are validated before uploads, and uploaded cells are read back for verification.

The email contains Colorado, Texas, and Arizona headings, one line per store
with Labor and Goal, then `Thanks,` and `AB`. Sales and transaction figures are
in the attached workbooks only. The email has three
regional Excel attachments and subject `Daily Sales & Labor YYYY-MM-DD`.

From: **ab@caazllc.com**

To: ab@caazllc.com, alicia@lccollc.com, kerry@lccollc.com,
carter@franbiznetwork.com, alex@caazllc.com.

Cc: jeff@caazllc.com, caleb@lccollc.com, wilder@lccollc.com.

## Schedule and manual runs

The target is daily **08:00 America/Los_Angeles**, reporting the previous day.
UTC checks at 15:00, 16:00, 18:00, 20:00 and 22:00 cover daylight saving,
delayed runs and temporary portal failures.
The Pacific time gate skips early runs; the delivery receipt skips dates already
sent. GitHub jobs may start late, so exact 08:00 execution is not guaranteed.
`SALES_AUTOMATION_ENABLED=true` is enabled. Do not enable the old schedule too.

GitHub Actions -> **Daily sales report** -> **Run workflow**:

- `preflight`: check Gmail, workbook layouts, and all store metrics without writes
  or email.
- `preview`: prepare three workbook copies and `email-preview.eml` without uploads
  or email.
- `deliver`: update the original workbooks when needed and send the report.
- `repair-preview`: reread every store for `report_date` through `end_date`
  inclusive and prepare corrections, backups and a cell audit without uploading
  or emailing. Both dates use YYYY-MM-DD; blank end date means start date only.
- `repair`: perform the same source checks, then correct the existing workbook
  input cells. No email is sent, including for dates already delivered.

Scheduled deliveries also check the preceding seven report dates for blank
sales/transaction cells and fill them from fresh Altametrics reports. Existing
nonblank historical values are preserved by this automatic catch-up; use
`repair` for known incorrect values. Only the current daily email is sent.
An older unresolved gap keeps the run failed even if the current email succeeds.

Repair accepts at most 31 completed dates. It reuses one login per region,
requires all 18 stores on every requested source date, and validates all three
workbooks before any upload. It never changes formulas, date rows, store labels,
styles or other workbook parts. Uploads require the original OneDrive version
and every changed cell is read back. The private artifact includes original
and corrected workbooks, fresh source figures, and `repair-audit.json` with
each cell's previous and corrected value. `complete: true` and `verified: true`
for each workbook confirm a live repair. A preview intentionally does not verify
uploads. If a later upload fails, earlier verified uploads remain recorded;
rerunning the same dates safely compares them with fresh source data.

Expired OneDrive signed download URLs are renewed through authenticated Graph
content requests. Read-only transient transport failures are retried; version
conflicts and uploads are not blindly retried. Altametrics interactive security
checks still stop the affected run and are never treated as valid reports.

Leave the date blank for yesterday Pacific, or enter a completed date explicitly.
The private `sales-delivery-state` branch records sends. If SMTP has an uncertain
outcome, check Gmail Sent before clearing a receipt; automatic retries do not
risk sending a duplicate. Private report artifacts are retained for 14 days.

## Connections and maintenance

The current workflow requires `MS_CLIENT_ID`, `MS_REFRESH_TOKEN`, and
`CAAZ_GMAIL_APP_PASS`. The six regional Altametrics username/password secrets are also required.
Python and Chromium are installed on the GitHub runner; no local installation
is required for scheduled runs.

For Microsoft reconnection, use the existing app and authorized workbook account:

```powershell
python -m pip install -r 'sales automation/requirements-setup.txt'
python 'sales automation/connect_onedrive.py' --client-id YOUR_MICROSOFT_APP_ID --github
```

This saves encrypted GitHub secrets without writing a plaintext token file.
The public-client Microsoft app needs delegated Files.ReadWrite and offline_access,
and support for the personal Microsoft account that owns the workbooks.

Tests: `python -m unittest discover -s "sales automation" -p "test_*.py" -v`.

Prior failed approaches and connection checks are preserved in
[CONNECTION_HISTORY.md](CONNECTION_HISTORY.md). Do not treat historical setup
instructions as the current daily configuration.
