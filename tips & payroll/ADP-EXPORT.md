# Regional ADP CSV preparation

`adp_export.py` fills original regional ADP CSVs from verified TCP payroll and
Gateway tips. It runs from LC-AUTOMATION, without another repository. It does
not submit payroll to ADP or send email.

Only `Reg Hours`, `O/T Hours`, and `TIP` change. Company, batch, file number
(including leading zeros), description, name and rate remain as exported by ADP.
All `!` control rows retain their original text. The hours/tips earnings metadata
must match the supplied nine-column EPIFND00/EPIFNC00 format.

Inputs are a verified `payroll-input.json`, same-period verified
`gateway-input.json` covering all 18 stores, and fresh regional ADP CSVs with
verified identity mappings. Start with `adp-mapping.example.json`, stored privately
beside the CSVs. Each employee mapping has text fields `region`, `geid`,
`company_code`, and `file_number`. Templates specify a relative `file`, company,
batch and SHA-256. The hash binds verification to the exact source export.
Names alone never establish an export identity. Do not mark sample/name-only
mappings verified, clear real issues, invent IDs or omit unmatched employees.

Every payroll employee/region needs one mapping, and every employee in every ADP
template must be covered, including zero-hour staff. Differences stop all three
exports. Hours combine across stores within each region under the verified ADP
identity. TCP's OT classification is preserved. Cross-region employees require
separate regional mappings. Management receives no tips.

```powershell
python 'tips & payroll/adp_export.py' `
  'tips & payroll/reports/PERIOD/payroll-input.json' `
  'tips & payroll/reports/PERIOD/gateway-input.json' `
  'tips & payroll/data/PERIOD/adp-mapping.json' `
  'tips & payroll/reports/PERIOD/adp-export-1'
```

The CLI requires a completed 14-day period and a new output directory. All
regions are validated before files are written. Each regional subfolder retains
the original CSV filename. The audit records source/output hashes, employee
counts, totals and `submitted: false`. Original files are not overwritten.

September 20 checks: the parser accepted the supplied originals (193 FND and
154 FNC employee rows). Synthetic tests cover preserved control rows, leading
zeros, names/rates, encodings, cross-store manager hours, zero-hour staff, full
roster coverage, and rejected invalid/unverified inputs. No live import-ready
payroll was generated because mappings and final TCP checks remain unresolved.

Live ADP connection, fresh roster retrieval and automated import remain unfinished.
`ADP_CLIENT_ID` and `ADP_CLIENT_SECRET` are missing; certificate/private-key
secrets are present. Manual CSV preparation does not require the ADP API, but
still requires verified current regional exports and final payroll inputs.
