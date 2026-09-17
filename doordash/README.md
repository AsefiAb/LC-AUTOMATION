# DoorDash disputes

Migrated from the Desktop `doordash` folder into `AsefiAb/LC-AUTOMATION`.
The original folder and its existing **DisputeApp DoorDash 2pm** Windows task
are unchanged. This replacement has not submitted a live dispute or sent an email.
The original Uber Eats/Grubhub automations remain in that original folder.

## What changed

- Review dashboard imports a real scan plan and exports approved descriptions.
  It no longer claims to have submitted disputes when it only changed local state.
- A persistent ledger records an attempt **before** the final submit click.
  Repeated or uncertain submissions are not automatically retried.
- One local lock prevents overlapping replacement runs. Corrupt history stops a run.
- Reads paginate through recognized Next-page controls; waits respond to page
  content instead of fixed delays on every step. Navigation retries are bounded.
- Outcome checks revisit saved transaction URLs, including charges that disappear
  from the filtered transaction list after approval. Resolved orders are skipped.
- Monetary totals use integer cents. Tracker categories distinguish approved,
  denied, pending, uncertain and not-yet-verified; no estimated win rates are used.
- Descriptions request review of the records. No inspection, packing, driver fault
  or customer refund-abuse claims are invented from customer order counts.
- Credentials, browser profiles, screenshots, reports and history stay out of Git.

## Current validation and limits

The dashboard builds and automated tests cover accounting, duplicate protection,
uncertain responses, email receipts, imports and an isolated simulated portal.
The replacement selectors still need a read-only check in the live merchant
session. Portal changes fail visibly rather than reporting a successful empty scan.
The initial local connection check timed out attaching to Automation Chrome;
its existing session needs to be made responsive before a live scan can finish.
Cloudflare/login/2FA and cooldown challenges require the owner to finish them.

The scan uses the **current portal date range**; it does not choose a new range.
Verify the date filter and any virtualized/unrecognized pagination before treating
the scan as a complete period. Multiple distinct amounts for one order are stopped
for review. Current selectors require an accessible dispute dialog and reason menu.

The migrated store labels/IDs come from the original scripts. They have not been
independently reconciled with restaurant payroll store numbers.

## Local setup and usage

Node 20+ and Python 3.10+ are required. In PowerShell, from this folder:

```powershell
npm ci
npm ci --prefix scraper
python -m pip install tzdata==2025.3
```

Open the existing **DoorDash Automation Chrome** shortcut and sign into DoorDash.
The scripts connect only to the local Chrome debug endpoint, by default
`http://127.0.0.1:9222`. They do not launch or close your normal Chrome window.

1. Import the old history once, and again before cutover if the old task has filed
   more orders. Existing records are preserved; imported outcomes start unverified:

   ```powershell
   node scraper/import-history.mjs "C:\Users\abase\OneDrive\Desktop\doordash\screenshots\filed-disputes.json"
   ```

2. Scan without submitting. Region/store filters are optional:

   ```powershell
   node scraper/run.mjs --mode scan --stores CO
   npm run dev
   ```

   Upload `data/review-plan.json` into the dashboard. Review descriptions, approve
   the appropriate orders, and **Export approved** to download `approved-plan.json`.
   A payout CSV can also be imported, but CSV drafts need manual portal entry
   because they lack verified merchant transaction links. Keep the exported file
   until the run finishes; dashboard edits are not persisted across reloads.

3. Once the original DoorDash task is stopped and its latest history imported,
   submit that reviewed plan. `--limit` caps new attempts; rerun the same file to
   continue the remainder, without resending recorded orders:

   ```powershell
   node scraper/run.mjs --mode submit --plan "C:\Users\abase\Downloads\approved-plan.json" --limit 50
   ```

4. Refresh outcomes and build the cumulative tracker:

   ```powershell
   node scraper/run.mjs --mode outcomes
   node scraper/report.mjs
   ```

   Reports are saved under `reports/`. Approval is a portal status; payment to the
   bank is not independently verified. The Orders sheet contains check timestamps.

## Email to Carter and Alicia

Configured in `email-config.json`:

- From: **ab@caazllc.com**
- To: **carter@franbiznetwork.com**, **alicia@lccollc.com**
- Attachment: the generated DoorDash XLSX tracker.

The mailer uses `CAAZ_GMAIL_APP_PASS`, already present in LC-AUTOMATION GitHub
secrets. GitHub secrets are available to Actions jobs, not to a local PowerShell
process. A local run needs the same app password in its environment.

```powershell
python email_report.py "reports\DoorDash-Tracker-TIMESTAMP.xlsx" --preview
# Without --preview, sends the real message using CAAZ_GMAIL_APP_PASS.
```

Email is limited to one send attempt per Pacific day. An SMTP timeout or partial
rejection leaves a `sending` receipt. Check Gmail Sent and recipient results before
manually resolving that receipt; do not delete it merely to rerun the job.

### Use the existing GitHub secret with this PC

The repository currently has no self-hosted runner. To connect it:

1. GitHub **Settings → Actions → Runners → New self-hosted runner → Windows**.
   Follow GitHub's displayed download and configuration commands in a separate
   folder outside this project. Add custom label **doordash**.
2. Run it as your signed-in Windows user with access to the existing Chrome
   session; the PC must remain on and Chrome must be logged in.
3. Set repository variable `DOORDASH_DATA_DIR` to the migrated persistent folder,
   e.g. `C:\Users\abase\OneDrive\Desktop\TCP AUTOMATION\doordash\data`.
   It must be outside the runner's checkout directory.
4. After the live read-only check succeeds, set `DOORDASH_RUNNER_ENABLED=true`.
5. Run **DoorDash outcomes and email** from Actions. It refreshes outcomes, builds
   the tracker and sends to the requested recipients. Uncheck `send_email` for
   the first connection test. This workflow never submits disputes.

No new daily schedule has been activated. The original task also runs Uber scripts;
plan its cutover before disabling it to avoid unintentionally stopping those jobs.
Never run the old and new DoorDash submission engines simultaneously: the old
engine does not honor the new lock or ledger.

## Recovery and testing

- Unknown confirmation: run outcomes; retain the attempt until its status is known.
- Interrupted process: inspect `data/run.lock`; remove it only after confirming
  the recorded process is no longer running. Lock files never expire automatically.
- `DOORDASH_DATA_DIR` may point outside this checkout for durable history. Back it
  up; GitHub does not store the ledger. Import does not overwrite known outcomes.
- `CDP_URL` overrides the local debugging port. Store filters accept region,
  original label (e.g. `AZ-5`) or numeric merchant store ID.

```powershell
npm run build
node --test tests/dashboard.test.cjs
npm test --prefix scraper
python -m unittest discover -s . -p test_email.py -v
```

The portal test intercepts all requests and uses fake orders. It never contacts
DoorDash or sends disputes. GitHub **DoorDash checks** runs these tests on pushes.
