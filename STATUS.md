# Operational status — September 20, 2026

The replacement automations are built, but automatic delivery is not enabled.
Keep the old repositories and working schedules until a verified cutover.

LC-AUTOMATION is the intended sole operating repository. Payroll, Gateway tips,
sales, email and DoorDash implementations are local to this repo. Old repos
remain source references and rollback copies until the cutover is verified.

| Area | Verified | Still needed |
| --- | --- | --- |
| Payroll tips | Gateway totals for all 18 stores; decimal-hour allocation; Crew Leaders included; managers excluded; store and combined report tests | Confirm the outstanding clock-outs and one employee's job title; fresh completed-period reconciliation |
| TCP hours | Native Complete Payroll export exactly matched all 148 September 14 shifts and restored missing regular/OT splits locally | XML download remains a manual fallback; TCP must fix the API discrepancy for unattended operation |
| Email | Gmail authentication succeeded from the replacement workflow; recipients configured | First complete, validated payroll/sales output and delivery verification |
| OneDrive | All three workbooks downloaded successfully; Colorado store headers corrected; date/store layout checks passed | No additional Microsoft secrets needed |
| Sales | Replacement code, requested plain-text email layout and tests; Gmail and existing workbook connections | Altametrics still requests interactive verification. The old workflow updated workbooks but its Gmail send failed despite a green run. |
| ADP | Regional CSV builder preserves the original layout, roster, identities, rates and control rows; synthetic tests pass | Verified current-period mappings and CSVs, final TCP payroll, and live ADP connection/import remain outstanding |
| DoorDash | Migrated code and tested safeguards | No GitHub self-hosted runner is registered. The old Windows task still points to the old Desktop folder; live outcome check and cutover remain unfinished. |

Payroll is configured for every other Tuesday at 8 a.m. Pacific, starting
September 22 for September 8–21 inclusive. Sales is configured for daily 8 a.m.
Pacific. Neither replacement delivery schedule is enabled yet.

The remaining payroll facts are actual clock-out dates/times for Gable Tokoph
(September 19, 10:48 a.m. start) and Jaydon Duckett (September 19, 11:14 a.m.
start), and Maximo Cano's actual job title/employment status. Alicia Wright's
September 7, 4:02 p.m. open punch also needs historical correction; it starts
outside the upcoming payroll period. Never guess these values.

The native report fallback restored 148/148 calculated splits and left zero
closed zero-hour shifts in the historical diagnostic. It still stopped on the
remaining issues; no incomplete payroll was emailed. The upcoming period is still
open on September 20 and requires fresh sources after September 21 ends.

Validation: 53 payroll tests (including 8 ADP export tests) and 23 sales tests passed locally. Sales live check
[35542323649](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35542323649)
confirmed Gmail and all three workbook connections and stopped at Altametrics
interactive verification. It sent no email and changed no workbook values.

See [Payroll runbook](tips%20%26%20payroll/PAYROLL-RUNBOOK.md) for the native-export
fallback and preview steps. Private audits, native exports, and correction lists
are kept outside Git under ignored reports folders.
