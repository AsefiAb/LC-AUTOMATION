# Operational status — September 22, 2026

Payroll's scheduled delivery is enabled, with data validation before any email.
Sales delivery remains disabled. A schedule being enabled does not establish that
all live data sources can complete unattended.
Keep the old repositories and working schedules until a verified cutover.

LC-AUTOMATION is the intended sole operating repository. Payroll, Gateway tips,
sales, email and DoorDash implementations are local to this repo. Old repos
remain source references and rollback copies until the cutover is verified.

| Area | Verified | Still needed |
| --- | --- | --- |
| Payroll tips | September 8–21 Gateway totals for all 18 stores; formula-based Excel verified for every employee allocation; crew and Crew Leaders only, with tips kept in each store | Prepared reports have not yet been emailed |
| TCP hours | Fresh full-period native export matched all 2,325 API shifts and restored 280 calculated splits; 424 period-roster people retained | API still returns those zero splits after generating the report; a native export remains necessary for this discrepancy |
| Email | Gmail authentication succeeded; payroll Excel attachments and combined employee/manager email prepared privately | Send and verify the prepared catch-up payroll; sales is still blocked by its data source |
| OneDrive | All three workbooks downloaded successfully; Colorado store headers corrected; date/store layout checks passed | No additional Microsoft secrets needed |
| Sales | Replacement code, requested plain-text email layout and tests; Gmail and existing workbook connections | Altametrics still requests interactive verification. The old workflow updated workbooks but its Gmail send failed despite a green run. |
| ADP | Regional CSV builder exists; no import performed | Owner is handling imports; ADP integration is on hold |
| DoorDash | Migrated code and tested safeguards | No GitHub self-hosted runner is registered. The old Windows task still points to the old Desktop folder; live outcome check and cutover remain unfinished. |

Payroll is configured for every other Tuesday at 8 a.m. Pacific, starting
September 22 for September 8–21 inclusive. Sales is configured for daily 8 a.m.
Pacific. Payroll is active with `PAYROLL_AUTOMATION_ENABLED=true`; its next dates
are October 6, October 20 and November 3, with retries and duplicate-send receipts.
Sales remains in validation mode. GitHub may delay scheduled job starts.

The current September 8–21 snapshot has no open punches. Two people with no
worked shifts have no operational role; one also lacks a home store. They are
included with zero hours and tips and explicit setup notes. Eleven people
terminated before the period with no worked shifts are listed separately for
roster completeness. No timecards or personnel assignments were edited.

Validation: 56 payroll tests passed, plus the additional cross-store crew
allocation regression test; all 18 Excel tip pools reconcile to Gateway and all
employee Excel calculations match the independent allocation. The earlier 23 sales tests passed locally. Sales live check
[35542323649](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/35542323649)
confirmed Gmail and all three workbook connections and stopped at Altametrics
interactive verification. It sent no email and changed no workbook values.

See [Payroll runbook](tips%20%26%20payroll/PAYROLL-RUNBOOK.md) for the native-export
fallback and preview steps. Private audits, native exports, and correction lists
are kept outside Git under ignored reports folders.
