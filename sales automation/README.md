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

## Status October 12, 2026: scheduled delivery stalled since October 6

Delivery receipts exist through September 29, then only October 2 and October 4.
Private run artifacts show two different Altametrics blocks on the GitHub runner:

- **October 2–9 (runs #41, #43, #50–#55)**: the regional login showed a reCAPTCHA
  *Security Check*. The adapter never completes challenges, so those runs stopped.
- **October 9 20:00 UTC onward (runs #56–#62)**: no challenge. The SSO host
  showed a full-page *Scheduled Maintenance* announcement (October 14–15) with
  one submit control, and the adapter only acknowledged the one supplier notice
  it knew by text. Every login then timed out as `login redirect did not finish`.

`altametrics.py` now acknowledges a full-page SSO announcement only on
`apssopgs.altametrics.com`, only through its single `Submitbutton`, and only
when the page has no password or other entry field, contains one of
*important message, scheduled maintenance, maintenance, announcement, notice,
reminder*, and none of *password, passcode, one-time, agree, terms, accept,
consent, policy, privacy, license, conditions, security, verif(y/ication),
not a robot, captcha*. At most three acknowledgments happen per login attempt.
Anything else with a submit control is left alone and the run log states why
(`not acknowledged: page mentions "..."`, `no announcement keyword`, or
`page has entry fields`), so the next unknown page is diagnosable from the log.
Agreements, password prompts and CAPTCHAs are still never submitted.

The daily run also reads the current report before the seven-day catch-up. A
portal block then costs one login attempt per region instead of two; any other
current-report failure still runs the blank-cell catch-up before the run fails;
and an already-delivered date still lets later slots recover older gaps. The
owner notice names the catch-up window whenever it was not completed.

Workbook state before the October 12 repair: September 28–October 4 complete;
October 5 complete except 3356-03 (sales and transactions blank);
October 6–11 blank for all 18 stores (216 cells).
[Run 38331617990](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/38331617990)
(`repair-gaps`, October 5–11) then filled and read back **214 cells**
(Colorado 82, Texas 48, Arizona 84). Two store-days stay blank because the
dashboard has no report for them: **3356-03 on October 5 and October 7**
(*No Information present for this Selection*). Altametrics also reports
3356-03 at $0 with 0 transactions on October 6, which was written as reported;
use `repair` with a reviewed source if that store's figures are later corrected.
Daily catch-up re-reads those two store-days each morning until they leave the
seven-day window (October 15), which adds a few minutes and no failure.

October 13: no scheduled slot started all morning (all slots sat at the top of
the hour), so the October 12 report was dispatched manually. The first attempt
stopped because 3356-06 had no Daily Activity Report for October 12; after the
no-report tolerance above, [run 38458050446](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/38458050446)
delivered 17 stores at 12:55 PM Pacific with 3356-06 labeled
`No Altametrics report for this date` and its October 12 cells left blank for
the catch-up. The off-peak schedule from 07:47 AM Pacific starts October 14. The catch-up covers
only the seven dates before the report date, so the whole October 5–11 gap is
inside the window only for a scheduled success on October 12 or 13; from
October 14 the window drops one older date per day, and dates outside it produce
no failure or notice. If no scheduled delivery has succeeded by October 13, run
`repair-gaps-preview` then `repair-gaps` with report_date `2026-10-05` and
end_date `2026-10-11`, or use the owner sign-in capture below, which already
uses that range.

Altametrics has announced an outage October 14, 10:00 PM PT through
October 15, 4:00 AM PT; the 8:00 AM October 15 run is after it.

**Password expiry, October 12 preflight ([run 38324970807](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/38324970807)):**
with the announcement fix, all three regional logins succeeded and then landed
on a *Create New Password* page stating "Your password will expire in 5 days".
The adapter never changes passwords. It now uses that page's own postpone
control (Skip, Later, Remind me later, Not now, Cancel and similar labels) once
per login and otherwise stops with `requires a password change`, which the
owner notice reports as its own cause. The page offers *Change Password* and
*Cancel*; [run 38326050185](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/38326050185)
confirmed that *Cancel* reaches the dashboard and read all 18 stores for
October 11, leaving only a TCP wage-rate gap for 3356-03 that labels that
store's labor as unavailable without blocking delivery. The owner must change each regional
Altametrics password in the portal before it expires (about October 17) and
then update `COLORADO_PASSWORD`, `AZ_PASSWORD` and `TX_PASSWORD` in GitHub
Secrets. Until both steps are done, scheduled delivery stays blocked once the
passwords expire.

## Reviewed source capture (owner sign-in, no stored credentials)

When GitHub-hosted logins are challenged, the owner can sign in once per region
on their own computer and let the helper read every store for a date range:

```powershell
python -m pip install -r 'sales automation/requirements.txt' -r 'sales automation/requirements-setup.txt'
python -m playwright install chromium
python 'sales automation/review_sales_source.py' --region CO --start 2026-10-05 --end 2026-10-11
python 'sales automation/review_sales_source.py' --region AZ --start 2026-10-05 --end 2026-10-11
python 'sales automation/review_sales_source.py' --region TX --start 2026-10-05 --end 2026-10-11
```

Each command opens a visible browser, waits for the regional dashboard, then
saves `reports/reviewed-source/<REGION>-<start>_<end>.json` with Product Net and
Actual Total transactions per store and date. A store/date with no report rows is
retried once, then recorded as explicitly unavailable, never as zero. Nothing is
uploaded, and no session or password is saved.

Then combine, validate, encrypt and start a blank-cell-only repair:

```powershell
python 'sales automation/build_reviewed_source.py' --start 2026-10-05 --end 2026-10-11 --reviewed-by "AB" --upload --dispatch repair-gaps-preview
```

The builder applies the same validation as the GitHub repair, prints the
SHA-256 that binds the dispatch to the exact secret bytes, saves the payload to
`SALES_REPAIR_SOURCE_JSON` through the local Git credential, and starts the
workflow. Review the private `repair-audit.json`, then rerun the same command
with `--upload --dispatch repair-gaps` to write only blank cells:

```powershell
python 'sales automation/build_reviewed_source.py' --start 2026-10-05 --end 2026-10-11 --reviewed-by "AB" --upload --dispatch repair-gaps
```

`--dispatch` always requires `--upload` so the dispatched digest matches the
stored secret; re-uploading re-encrypts identical bytes and prints the same
digest. The payload must fit GitHub's 48 KB secret limit (about three weeks of
all 18 stores); split longer ranges. Existing values are never overwritten by
the gaps modes; use `repair` for known incorrect values. Both jobs, hosted and
local runner, honor the reviewed source in every repair mode.

## Local runner fallback

If Altametrics keeps challenging GitHub-hosted logins, the verified local mode
from September 23 remains available: register the owner's Windows runner with
label `lc-sales-interactive`, open the three dedicated signed-in browsers with
`python 'sales automation/live_browser.py' --open-all`, and set repository
variable `SALES_RUNNER=local`. The `report-local` job then runs the same modes
with `ALTAMETRICS_AUTH_MODE=local-live`, reading the open dashboards without
storing credentials. It runs under the built-in Windows PowerShell 5.1, so
PowerShell 7 is not required. Remove the variable to return to GitHub-hosted
runs. Do not set it without a registered runner, or scheduled jobs queue until
timeout.

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

`ALTAMETRICS_AUTH_MODE=cloud-legacy` uses the original repo's SSO form
submission, with bounded retries for stalled redirects. Security overlays are
never removed; interactive challenges stop the affected collection.
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

Ambiguous metrics, wrong dates, conflicting existing cells, or unexpected
workbook layouts stop delivery. All three files are validated before uploads,
and uploaded cells are read back for verification. Since October 13 a store
whose dashboard states *No Information present for this Selection* for the
report date no longer blocks the other 17: its email line reads
`No Altametrics report for this date`, its workbook cells stay blank, the run
log and `unavailable-stores.json` name it, and the daily catch-up fills the
cells if the report appears within seven days. Any other missing or malformed
metric still stops delivery; a blank is never written as zero.

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
Through October 12 the cron slots sat at the top of the hour, which GitHub
documents as its peak load: the 08:00 Pacific slot almost never started and
the email usually left between 12:15 and 1:00 PM. Slots now use off-peak
minutes: 14:47, 15:07, 15:27, 15:52, 16:22, 16:52, 17:07, 18:07, 20:07 and
22:07 UTC. `schedule_gate.py` (`run_time` in `config.json`) allows a run from
**07:45** local time in both PDT and PST, so the first eligible slot is 7:47 AM
in summer and 7:52 AM in winter. A slot that starts late still delivers once.
Earlier slots exit at the gate before installing anything. After a delivery,
`daily_report.py` notes the catch-up outcome on the date's receipt
(`catch_up: complete` or `failed`); the gate reads that receipt and later slots
exit in seconds unless the catch-up still needs a retry. A receipt read problem
never blocks a run; the delivery itself still checks the ledger before sending.
GitHub still does not guarantee the exact start time.
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
- `repair-gaps-preview` and `repair-gaps`: the same, but only blank dated cells
  are filled; existing values are left untouched and are not treated as
  conflicts. Both accept the reviewed source secret and digest.

Scheduled deliveries also check the preceding seven report dates for blank
sales/transaction cells and fill them from fresh Altametrics reports. Existing
nonblank historical values are preserved by this automatic catch-up; use
`repair` for known incorrect values. Only the current daily email is sent.
An older unresolved gap keeps the run failed even if the current email succeeds.
When the dashboard states *No Information present for this Selection* for a
store/date (for example 3356-03 on October 5), blank-cell recovery re-reads it
once, then keeps that cell blank and lists it under `unavailable` and
`unverified_cells` in `repair-audit.json` with `complete: false`, instead of
abandoning every other store and date. A blank amount is never written as zero.
The daily email and the `repair` correction modes still require every store.

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

Failed enabled scheduled runs send an owner-only notice to **ab@caazllc.com**,
once per report date. It identifies known blocked regions, links the GitHub run,
and distinguishes a failed current email from earlier workbook gaps after a
confirmed current delivery. It includes no stale workbook attachments or raw
portal URLs. A separate alert receipt prevents retry slots from repeating the
notice; the original workflow remains failed. Preview, repair and other manual
runs do not send these notices. SMTP failures or a runner stopping before the
notification step remain visible through GitHub's own run status.

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
