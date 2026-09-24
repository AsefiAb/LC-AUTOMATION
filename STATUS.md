# Operational status — September 23, 2026

Keep the old repositories as rollback copies until the remaining cloud cutovers
are verified. Successful tests do not mean every source can run unattended.

| Area | Verified | Remaining work |
| --- | --- | --- |
| Sales | All 18 stores and three existing OneDrive workbooks passed a GitHub-hosted preview in [run 35929515395](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35929515395). The preceding live delivery was accepted for all requested recipients. | Verify the next scheduled GitHub-hosted delivery and an actual workbook update. Values already matched in the cutover run. |
| Payroll delivery | September 8–21 packages were delivered through GitHub in [run 35916628671](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35916628671). Regional ADP attachments are CSV, with store Excel files and disclosed exceptions. | Current ADP roster exports are required for each new period while ADP API integration is on hold. Review regional issues before importing. |
| Gateway tips | A fresh [September 23 preview](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35933304786) retrieved all 18 stores for September 8–21. Tips stay within each store and go only to crew and Crew Leaders. | No Gateway access blocker in this check. |
| TCP hours | Native Complete Payroll exports can reconcile missing API calculations by exact employee, job and punches. | Fresh API preview failed: 281 closed zero-hour shifts, no calculated worked shifts on September 14, and one work-segment-only employee. A matching native export or TCP API correction remains necessary. |
| DoorDash | The July 23–September 22 audit checked all 520 orders across 18 stores. GitHub refreshed every status in [run 35944018274](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35944018274) and independently matched all 682 financial transactions in [run 35944353115](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35944353115), without a browser. Confirmed recorded credits: $1,056.89; 15% share: $158.53 for this period only. | 15 orders were not submitted, including three with an expired portal deadline. Recurring collection, claim submission and complete all-time billing are not enabled. No dispute email was sent. |
| Uber Eats / Grubhub | The supplied workbook is preserved in the multi-platform totals report. | Fresh dated refunds and unattended access are not connected. Uber account 1 returned zero stores; Grubhub outcomes remain unverified. |

## Schedules and local tasks

- Sales is enabled in GitHub for daily 8 a.m. Pacific. The old sales workflow is disabled, and the local sales runner/startup registration was removed.
- Payroll is enabled for alternate Tuesdays at 8 a.m. Pacific, anchored September 22. Next: October 6 for September 22–October 5, then October 20. Missing source hours still stop delivery.
- DoorDash delivery remains disabled until the verified read APIs are integrated into recurring collection and historical billing is reconciled. Intended cadence: the same alternate Tuesdays. First delivery includes verified historical unpaid earnings; later deliveries avoid double billing.
- The old local DoorDash sweep and its shared Windows task are disabled to honor the no-window requirement. Uber collection is paused until its cloud connection is ready. A task backup is retained locally.
- GitHub can delay scheduled starts. Delivery receipts prevent routine duplicate mail.

## What the owner still needs to provide

1. Ask TCP to make calculated API hours match Complete Payroll; the concrete [support request](tips%20%26%20payroll/TCP-SUPPORT.md) describes the discrepancy. Until resolved, supply a Complete Payroll XML for each exact pay period.
2. Export the current CO, AZ and TX ADP rosters each pay period so new employees have their actual ADP file numbers. No automatic ADP import is enabled.
3. Provide dated refund access for Uber Eats and Grubhub. DoorDash status and financial reads now work in GitHub with the authorized merchant session; session renewal and recurring ingestion still need implementation. Carter’s email inbox is not required for this verified read path.

The DoorDash historical export covers September 23, 2024–September 22, 2026,
not proof of history before that date. Four stores outside the configured 18 are
kept separate. The complete two-month audit found 137 refunded, 367 denied,
one pending and 15 unsubmitted orders. Two refunded orders have no posted credit
inside that period. Only dated credits matched to refunded statuses are counted.
The original 422-order ledger is not a complete audit universe and remains separate
from the new 520-order dated audit. No all-time AB invoice has been sent.

Private exports, payroll audits, employee details and reconciliation results stay
in ignored report folders or private workflow artifacts, not the code branch.
