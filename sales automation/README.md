# Daily sales reporting

Daily sales delivery is enabled in **AsefiAb/LC-AUTOMATION**. The original
**AsefiAb/salesautomation** daily workflow is disabled; keep that repo for history.

The source is **Altametrics**: Product Net sales, Actual Total transactions,
and Punched Labor %. TCP remains separate for payroll.

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
with Sales, Transactions, Labor, and Goal, then `Thanks,` and `AB`. It has three
regional Excel attachments and subject `Daily Sales & Labor YYYY-MM-DD`.

From: **ab@caazllc.com**

To: ab@caazllc.com, alicia@lccollc.com, kerry@lccollc.com,
carter@franbiznetwork.com, alex@caazllc.com.

Cc: jeff@caazllc.com, caleb@lccollc.com, wilder@lccollc.com.

## Schedule and manual runs

The target is daily **08:00 America/Los_Angeles**, reporting the previous day.
UTC checks at 15:00, 16:00, and 18:00 cover daylight saving and delayed runs.
The Pacific time gate skips early runs; the delivery receipt skips dates already
sent. GitHub jobs may start late, so exact 08:00 execution is not guaranteed.
`SALES_AUTOMATION_ENABLED=true` is enabled. Do not enable the old schedule too.

GitHub Actions -> **Daily sales report** -> **Run workflow**:

- `preflight`: check Gmail, workbook layouts, and all store metrics without writes
  or email.
- `preview`: prepare three workbook copies and `email-preview.eml` without uploads
  or email.
- `deliver`: update the original workbooks when needed and send the report.

Leave the date blank for yesterday Pacific, or enter a completed date explicitly.
The private `sales-delivery-state` branch records sends. If SMTP has an uncertain
outcome, check Gmail Sent before clearing a receipt; automatic retries do not
risk sending a duplicate. Private report artifacts are retained for three days.

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
