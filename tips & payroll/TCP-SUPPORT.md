# TCP calculated-hours discrepancy

Please investigate the read-only Hours API for our lcoperations company.
For the inclusive September 8–21, 2026 pay period, the September 23 API check
returned 281 closed shifts with zero calculated hours and no calculated worked
shifts on September 14. The native Complete Payroll report previously supplied
calculated regular and overtime hours for affected shifts.

Our request uses `/v1/hours`, `startDate=2026-09-08`, `stopDate=2026-09-22`
(one-day overnight buffer), `includeJobCode=true`, `includeRate=false`, and
`includeOverlapping=false`. We paginate through an empty page and locally keep
only shifts starting September 8–21. We compare IDs and punches against
`/v1/worksegments` for the same inclusive period.

Please confirm:

1. Whether archived/calculated payroll data requires different filters,
   permissions, an endpoint, or a company setting.
2. Why the Hours API differs from the native Complete Payroll report, and how
   to obtain the report's calculated regular and overtime splits unattended.
3. The correct date-boundary rules and whether generating a native report is
   expected to refresh API calculations.

One work-segment-only employee also remains in the response. We retain that
as a separate exception rather than guessing payroll from clock timestamps.
We can provide the private record-level correction list through your secure
support channel. API credentials and employee records should not be posted publicly.

Reproduction: private GitHub preview run 35933304786 on September 23, 2026.
No timecards were changed and no payroll email was sent by this diagnostic run.
