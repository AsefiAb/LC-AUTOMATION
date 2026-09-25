# Labor formula verification - September 24, 2026

Altametrics sources inspected while signed into Arizona:

- Labor -> Timekeeping -> Weekly Timekeeping.
- Reports -> Multi Store Report -> Labor -> Labor Summary and Detail by Department - Select Store.
- Daily Activity Report -> Actual Total -> Product Net and Punched Labor %.
- Admin -> Organization Admin -> Store Admin -> Operations: Store Hours,
  Labor Law/People Practice, and Salaried and Accrual Settings.

## Reconciled examples: 3759-01

September 15-21, 2026: regular pay $8,021.75 + overtime pay $202.17 =
$8,223.92. Dividing by $39,166.16 net sales gives **21.00%**, matching
Weekly Timekeeping. The detail includes crew, assistant managers, co-managers
and managers; excluding management would not reproduce this percentage.

September 21, 2026: detail wages **$1,137.14** / Product Net **$3,697.84**
x 100 = **30.75%**, matching Daily Activity Report's Punched Labor %.

## Settings across all 18 stores

[Read-only GitHub audit](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/36075765148)
confirmed all seven Colorado, seven Arizona and four Texas stores:

- Store business day: 04:00 today to 04:00 tomorrow, every weekday.
- Overtime percentage: 150%.
- Daily salaried exempt manager dollars and additional daily accrual: $0.00.

The reviewed results are stored in `labor_settings.json`. The audit did not
save settings, edit punches, upload workbooks or send email.

## What the automation reproduces

The cost definition and business-day boundary. It uses TCP's calculated
regular/OT hours and returned rates, and Altametrics net sales. It includes all
worked roles, and allocates whole shifts by local starting business day. Native
TCP September 23 reports also assigned September 24 starts at 03:51 and 03:58
to September 23; those hours must not be dropped or treated as September 24.

The automation does not claim its TCP-based percentage must equal the
Altametrics percentage. Altametrics detail rounds hours and payroll components,
and its punches/rates may differ or be missing. This is why email retains
`(TCP estimate)`. Source gaps produce an unavailable reason rather than 0%.
Unknown OT2 multipliers and nonzero salary/accrual settings require reconciliation
before a store's estimate can be published. Taxes and benefits are excluded.

The audited settings are a snapshot; rerun the settings audit after changes.
Payroll/tip allocation rules are separate and unchanged by this sales update.

## Full GitHub preview after the change

[September 23 preview](https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/36076501165)
passed at commit cbb5d90, including all 62 tests. It collected all 18 stores,
prepared three regional Excel attachments, and created the plain-text email.
All 15 available percentages were independently recalculated from saved wage
and net-sales totals; the three MIME attachments matched the generated files.
No email was sent and no OneDrive workbook was uploaded by this preview.

Remaining source exceptions in this snapshot:

- 3759-03: open punches; labor unavailable.
- 3910-01: missing usable wage rate; labor unavailable.
- 3356-05: zero sales and no returned shifts; labor N/A.

3759-01 now includes 114.78 hours, matching the native TCP reporting-day total.
Estimated wages are $2,004.20 versus native $2,004.19 (one-cent component rounding);
both give 41.68% on $4,808.54 net sales. This verifies that example, not that
all Altametrics and TCP source records are identical. High or differing figures
still require checking the underlying punches and rates.
