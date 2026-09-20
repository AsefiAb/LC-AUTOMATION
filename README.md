# Restaurant operations

Shared workspace for restaurant automation projects.

```text
tips & payroll/        TCP hours and tips reporting (current focus)
sales automation/      Sales integrations and reporting
dispute tracker/       Dispute tracking
doordash/              DoorDash dispute review, outcome tracking, and email reports
```

## Credentials and data

Commit code, documentation, and blank `.env.example` templates only.
Keep real credentials in each project's local `.env` file. The root
`.gitignore` excludes these files from Git, along with `data`, `exports`,
`reports`, and `logs` directories. Do not force-add ignored files.
Git ignore rules do not encrypt files or prevent cloud-folder synchronization.

For a fresh checkout, copy the project's `.env.example` to `.env` and fill
in the values locally. Credentials are not included when cloning the repository.

Start with [tips and payroll](tips%20%26%20payroll/README.md).
Payroll workflow and setup: [Payroll runbook](tips%20%26%20payroll/PAYROLL-RUNBOOK.md).
Altametrics sales workflow and setup: [Sales reporting](sales%20automation/README.md).
DoorDash setup and migration status: [DoorDash](doordash/README.md).
