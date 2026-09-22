# Daily sales reporting

The configured source is **Altametrics**: Product Net sales, Actual Total
transactions and Punched Labor %. TCP is used separately for payroll.
The pipeline updates the same three CO/TX/AZ OneDrive workbooks and emails the
updated copies daily for the previous date. All 18 stores are required.

## Connections

Add repository secrets in **AsefiAb/LC-AUTOMATION**, not the old repository:

| Region/service | Secrets |
| --- | --- |
| Colorado Altametrics | `COLORADO_USERNAME`, `COLORADO_PASSWORD` |
| Arizona Altametrics | `AZ_USERNAME`, `AZ_PASSWORD` |
| Texas Altametrics | `TX_USERNAME`, `TX_PASSWORD` |
| Existing OneDrive workbooks | `MS_CLIENT_ID`, `MS_REFRESH_TOKEN` |
| Google Workspace sender | `CAAZ_GMAIL_APP_PASS` |

All six Altametrics secrets, the Gmail app password, MS_CLIENT_ID and
MS_REFRESH_TOKEN are present as of September 20, 2026. Microsoft device sign-in
and the live refresh-token exchange succeeded. The live check downloaded all
three existing workbooks successfully without changing them. Microsoft setup
is complete; portal extraction and workbook writes are separate checks.
Gateway credentials belong to payroll and are never substituted here.

Live check on September 20: Gmail SMTP authentication succeeded. All three
Altametrics accounts reached a visible CAPTCHA Security Check in GitHub Actions,
so store data could not be collected. Private masked screenshots confirm the
challenge. Approved unattended access from Altametrics, or an interactive local
session with a human completing verification, is needed. Completing a challenge
on a laptop does not necessarily authorize a fresh GitHub runner.

[Live check](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35535705207).

### Connect an existing Altametrics login

The local helper opens a clean browser for the account owner to sign in and
complete verification. It checks the selected region, closes the sign-in browser,
tests reuse in a fresh headless browser, and encrypts the latest verified session into this private
repository's GitHub Secrets. It saves no session file and sends no report.

```powershell
python -m pip install -r 'sales automation/requirements.txt' -r 'sales automation/requirements-setup.txt'
python -m playwright install chromium
python 'sales automation/connect_altametrics.py' --region CO
python 'sales automation/connect_altametrics.py' --region AZ
python 'sales automation/connect_altametrics.py' --region TX
```

Run each command separately and finish that region's login. These create
`CO_ALTAMETRICS_SESSION`, `AZ_ALTAMETRICS_SESSION`, and `TX_ALTAMETRICS_SESSION`.
The reporting job uses a saved session when present; otherwise it uses the
existing regional username/password. An expired or rejected saved session stops
the job and needs the same helper to reconnect. It does not fall back to repeated
password attempts or dismiss security checks.

Local sign-in success does **not** establish that GitHub runners can use the
session. Run `preflight` in GitHub Actions after connecting all three accounts.
Only a successful check of all 18 stores establishes that connection for that
run. Session expiration or vendor checks can require later reconnection; this
is not a promise of permanent unattended access. Keep delivery disabled until
source values and a full preview have been verified.

The September 20 Colorado session passed the original local verification but
GitHub returned **Session Timeout** in [the saved-session test](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35546424932).
Gmail and all three workbook reads passed. The helper now verifies after closing
the original browser and retains cookies updated during verification. These
changes improve the local check; cross-host session reuse remains unverified.

### Verified Texas local run

On September 20, the [Texas local connection check](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35547882984)
used the existing `TX_USERNAME` and `TX_PASSWORD` secrets on the owner's Windows
computer. It verified September 19 sales, transactions, and Punched Labor % for
all four Texas stores and saved `local-TX-2026-09-19.json` as a private artifact.
The updated session also passed a fresh headless browser check after sign-in
closed. No workbook writes or emails were performed.

`sales-local-texas.yml` is a manually dispatched, Texas-only diagnostic job. It
requires a Windows runner labeled `lc-sales-interactive`, running interactively
as the owner with Python, Playwright Chromium, setup dependencies, and GitHub
credential-manager access. The runner used for this check was ephemeral: it
handled one job, unregistered, and stopped. It was not installed as a service or
scheduled task. Its installation is under `%LOCALAPPDATA%/LC-Automation/sales-runner`,
outside the OneDrive workspace. The regular daily job remains gated off.

This demonstrates a working local Texas extraction, not ongoing unattended
access. Colorado and Arizona need equivalent current validation before the
complete 18-store daily delivery can be enabled. Human verification is still
required whenever Altametrics requests it. The local job supplies credentials
through its environment; it never stores plaintext credential files or uploads
credentials in report artifacts.

[Microsoft workbook read check](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35539325854).
The actual Arizona and Texas books have verified date/store destinations for
September 19. The user confirmed Constitution is `3356-03` and corrected the
TRANS header. The remaining SALES!G4 header was corrected to `3356-3` in Excel
Online on September 20, with the saved value verified. No sales figures changed.

For OneDrive, register a public-client Microsoft application for the account
that can edit the three files, enable public-client flows, and grant delegated
`Files.ReadWrite` and `offline_access`. Then run:

```powershell
python -m pip install -r 'sales automation/requirements.txt'
python 'sales automation/connect_onedrive.py' --client-id YOUR_MICROSOFT_APP_ID
```

Complete Microsoft's device sign-in. The helper writes the two values to the
ignored `sales automation/data/onedrive-secrets.json`; copy them into GitHub
Secrets. It does not print tokens. A client secret is not needed for this public
client. Local ignored files are not protected from OneDrive sync.

For direct encrypted upload to this private GitHub repo, using the local Git
credential manager instead of saving a token file:

```powershell
python -m pip install -r 'sales automation/requirements-setup.txt'
python 'sales automation/connect_onedrive.py' --client-id YOUR_MICROSOFT_APP_ID --github
```

Register the app while signed in as the business administrator. Authorize file
access separately with the account that edits the workbooks (the original setup
uses `abasefi1@outlook.com`). The personal Outlook account does not need access to
the Entra administration portal. Enable public-client flows for device sign-in;
the app must support personal Microsoft accounts. No client secret is needed.

## Run modes

GitHub Actions -> **Daily sales report** -> Run workflow:

- `preflight`: independently test Gmail authentication, read all three OneDrive
  books and validate date/store destinations, then test Altametrics extraction.
  Report missing connections and layout errors. No workbook uploads or email.
- `preview`: read the existing OneDrive files, prepare updated copies and email
  draft (`email-preview.eml`) in a private artifact. No workbook uploads or email.

The email body uses Colorado, Texas, and Arizona headings, one line per store
with Sales, Transactions, Labor, and Goal, followed by `Thanks,` and `AB`.
The report date remains in the subject. The three regional Excel workbooks stay
attached, and the configured To/Cc recipients remain unchanged.
- `deliver`: update the original files and send the report.

Missing/ambiguous totals, wrong dates, incomplete store coverage, unsupported
workbook layouts and changed existing values stop the run. Blank amounts never
become zero. Interactive login challenges require the account owner's attention;
the adapter does not bypass CAPTCHA or change passwords.

The workbook updater changes SALES and Trans cells matched by date and store,
retaining the XLSX package's formulas and formatting. It validates all three files
before uploading and re-reads each upload. A partial upload sends no email;
retries recognize matching cells. The report uses the source's Punched Labor %,
not an inferred labor cost.

## Email and schedule

From: **ab@caazllc.com**

To: ab@caazllc.com, alicia@lccollc.com, kerry@lccollc.com,
carter@franbiznetwork.com, alex@caazllc.com.

Cc: jeff@caazllc.com, caleb@lccollc.com, wilder@lccollc.com.

The target is daily 08:00 America/Los_Angeles. Checks at 15:00, 16:00 and 18:00 UTC
account for daylight saving and delayed GitHub jobs. Runs before local 08:00 skip.
GitHub scheduling can run late; exact 08:00 execution is not guaranteed.

`SALES_AUTOMATION_ENABLED=true` enables scheduled delivery. Leave it unset until a
live preview reconciles. When unset or false, due scheduled jobs run read-only
preflight checks and record failures instead of skipping the entire job. They
do not upload workbooks or send email. A private `sales-delivery-state` branch records each
report date, preventing duplicate email. An uncertain SMTP outcome requires
checking Gmail Sent before clearing the receipt; automatic retries never guess.

## Migration from the old repo

Keep **AsefiAb/salesautomation** until this replacement has produced matching data
for all 18 stores, updated the existing workbooks, and delivered a complete email.
For the controlled cutover, disable the old daily workflow before the first live
replacement delivery, then enable the replacement schedule. Re-enable the old
workflow if the cutover fails. Archive the old repo after successful replacement
runs; deleting it is unnecessary and removes useful rollback/history.

Tests: `python -m unittest discover -s "sales automation" -p "test_*.py" -v`.
Private artifacts are retained for three days. Credentials and report data are
not committed. The optional TCP adapter remains unused unless explicitly
configured with a verified sales/transactions/cost API; no such endpoint is
currently confirmed.
