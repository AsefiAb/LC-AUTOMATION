# DoorDash disputes and savings reports

The owner requested cloud-only operation. The workflow runs on GitHub-hosted
Ubuntu; it never opens a window on the owner computer. The local DoorDash sweep
is disabled. The original shared Windows task still runs its separate Uber Eats
jobs; those jobs can still open their own browsers.

## Live status

Cloud portal access is **not verified**. The first GitHub test stopped at DoorDash's
security verification page. A follow-up allows the normal automatic browser check
time to finish in a GitHub virtual display. No dispute or DoorDash email has been
sent by this replacement. Automatic reporting remains disabled until live access
and historical refund amounts are verified.

422 historical orders were imported into the private `doordash-state` branch.
Imported outcomes and refund amounts start unverified, never assumed won.
Browser login is held only in encrypted `DOORDASH_STORAGE_STATE` GitHub secret.
`CAAZ_GMAIL_APP_PASS` supplies the existing authorized sender connection.

## Requested email and calculation

- From **ab@caazllc.com** to **carter@franbiznetwork.com** and **alicia@lccollc.com**.
- Subject: **This week's dispute savings** (the owner's requested subject).
- Every 14 days, Tuesday at 08:00 America/Los_Angeles, anchored September 22, 2026.
  The next regular date is October 6, then October 20. GitHub can delay scheduled jobs.
- First successful email: all verified tracked historical refunds and all unpaid
  AB earnings. The owner confirmed no prior AB payments.
- Later emails: newly verified savings since the previous successfully sent report,
  with a 15% AB share. Previously reported savings are not billed again.
- Use actual verified refund amounts, including partial refunds. Original disputed
  amounts, pending disputes, denials, and unknown outcomes are not savings.
- Round cumulative 15% earnings to cents, then subtract previously reported earnings.
  This preserves cents across periods. A downward correction produces a credit.
- Portal approval is not bank-deposit confirmation. Recovery timing means when it
  was verified by this tracker; it can include an older dispute newly resolved.

The XLSX contains **Dispute savings**, **Issues**, **By store**, **Orders**, and
**Evidence review**. The email body shows the savings and **AB Cut: $X (15%)**;
the first also shows the unpaid total. An incomplete first historical reconciliation
blocks sending an inaccurate all-time bill. A preview and issues list remain available.

## GitHub operation

Run **DoorDash outcomes and email** with `send_email=false` for a read-only check.
It restores history, scans store charges, verifies previous outcomes/refund amounts,
creates the spreadsheet and retains private artifacts for three days.
Set `DOORDASH_AUTOMATION_ENABLED=true` only after a successful live reconciliation.
Manual `send_email=true` sends the first catch-up report using the same calculation.
The scheduled workflow subsequently sends every other Tuesday.

The cloud ledger preserves receipts before SMTP. A timeout or partial delivery
requires checking Sent before retrying; no automatic uncertain send is repeated.
History updates compare revisions; a missing history cannot become an empty ledger.
No cloud dispute submission is enabled. New charges go into an evidence review plan.

## Improve dispute quality

The review sheet separates the charged items, supporting records, applicable reason,
and reviewed explanation. Match each claim to actual receipt, preparation/packing,
pickup, or delivery records; do not infer customer fraud or invent proof.
Prioritize valid charges nearing their portal deadline, then larger recoveries.
DoorDash's [merchant instructions](https://help.doordash.com/en-us/merchants/article/what-are-order-error-adjustments)
describe choosing the relevant items/reason, checking the approved refund amount,
and a 14-day portal dispute window. Older cases need support review.
These improvements make claims more specific; they do not guarantee approval.

## Tests and historical tools

`npm test --prefix scraper` and `python -m unittest discover -s . -p 'test_*.py'`
cover portal fixtures, partial refunds, monetary rounding, first/subsequent reports,
Pacific scheduling, duplicate/uncertain submissions and email reservations.
The test portal is simulated; a passing test is not proof of live portal access.
Old local setup instructions are retained in CONNECTION_HISTORY.md as history only.
