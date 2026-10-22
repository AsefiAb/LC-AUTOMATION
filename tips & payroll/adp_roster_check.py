"""Compare the full ADP worker list with the payroll lists so nobody is missed.

Read-only. Inputs: the ADP API, the regional EPI templates (ADP_CO/AZ/TX_TEMPLATE
secrets, base64) and optionally a payroll-input.json from a payroll run. Writes
a private JSON and CSV listing, per region:
  - active ADP workers missing from the EPI CSV roster (new hires to add)
  - EPI CSV rows whose file number is not active in ADP (terminated or changed)
  - people with TCP hours this period that no active ADP worker matches by name
  - active ADP workers with no TCP hours this period (informational)
"""
import argparse
import base64
import csv
import json
import os
import sys
from pathlib import Path

from adp_api import COMPANIES, ADPError, fetch_workers
from adp_export import read_template
from adp_roster import FILES, name_key

REGION_COMPANY = {region: company for _, (region, company, _) in FILES.items()}
REGION_FRANCHISE = {region: franchise for franchise, (region, _, _) in FILES.items()}


def template_rows(region):
    value = os.environ.get(f'ADP_{region}_TEMPLATE', '')
    if not value:
        return None
    raw = base64.b64decode(value)
    company = REGION_COMPANY[region]
    text = raw.decode('utf-8-sig', errors='replace')
    batches = {line.split(',')[1].strip() for line in text.splitlines() if line.startswith(company + ',')}
    parsed = read_template(raw, company, next(iter(batches)))
    return {file_number: row for (_, file_number), row in parsed['employees'].items()}


def active(row):
    return row['worker_status'].lower() == 'active' and row['assignment_status'].lower() in ('a', 'active', '')


def names_for(row):
    keys = {name_key(f"{row['first_name']} {row['last_name']}")}
    if row['preferred_name']:
        keys.add(name_key(f"{row['preferred_name']} {row['last_name']}"))
    return keys


def compare(workers, templates, payroll, identities=()):
    from adp_api import current_workers
    confirmed = {i['geid'] for i in identities}
    report = {}
    for region, company in REGION_COMPANY.items():
        in_company = [w for w in workers if w['company_code'].upper() == company]
        live, _ = current_workers(workers, company)
        live_numbers = {w['file_number'].lstrip('0') for w in live if w['file_number']}
        section = {'adp_workers': len(in_company), 'adp_active': len(live)}
        template = templates.get(region)
        if template is not None:
            template_numbers = {n.lstrip('0') for n in template}
            section['epi_rows'] = len(template)
            section['missing_from_epi'] = sorted(
                ({'file_number': w['file_number'], 'name': f"{w['first_name']} {w['last_name']}",
                  'hire_date': w['hire_date'], 'job_title': w['job_title'], 'location': w['location']}
                 for w in live if w['file_number'].lstrip('0') not in template_numbers), key=lambda r: r['name'])
            section['epi_not_active_in_adp'] = sorted(
                ({'file_number': number, 'name': ' '.join(str(row[4]).split())}
                 for number, row in template.items() if number.lstrip('0') not in live_numbers), key=lambda r: r['name'])
        if payroll:
            adp_names = set().union(*(names_for(w) for w in live)) if live else set()
            people = {}
            for row in payroll['employees']:
                if row['store'].split('-')[0] == REGION_FRANCHISE[region]:
                    people.setdefault(str(row['employee_id']), row)
            section['tcp_people'] = len(people)
            section['tcp_not_in_adp'] = sorted(
                ({'geid': geid, 'name': row['name'], 'store': row['store']}
                 for geid, row in people.items() if geid not in confirmed and name_key(row['name']) not in adp_names), key=lambda r: r['name'])
            tcp_names = {name_key(row['name']) for row in people.values()}
            section['adp_active_without_tcp_hours'] = sorted(
                f"{w['first_name']} {w['last_name']}" for w in live if not names_for(w) & tcp_names)
        report[region] = section
    unknown = sorted({w['company_code'] for w in workers} - set(COMPANIES))
    report['other_company_codes'] = unknown
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payroll-input', help='payroll-input.json from a payroll run (optional)')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    workers = fetch_workers()
    (output / 'adp-workers.json').write_text(json.dumps(workers, indent=2), encoding='utf-8')
    templates = {region: template_rows(region) for region in REGION_COMPANY}
    payroll = json.loads(Path(args.payroll_input).read_text(encoding='utf-8')) if args.payroll_input else None
    from adp_identities import load
    identities, _ = load()
    report = compare(workers, {r: t for r, t in templates.items() if t is not None}, payroll, identities)
    (output / 'adp-roster-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    with open(output / 'adp-roster-check.csv', 'w', newline='', encoding='utf-8') as handle:
        out = csv.writer(handle)
        out.writerow(['Region', 'Check', 'File number / GEID', 'Name', 'Detail'])
        for region in REGION_COMPANY:
            section = report[region]
            for row in section.get('missing_from_epi', []):
                out.writerow([region, 'Active in ADP, missing from EPI CSV', row['file_number'], row['name'], f"hired {row['hire_date']} {row['job_title']}"])
            for row in section.get('epi_not_active_in_adp', []):
                out.writerow([region, 'In EPI CSV, not active in ADP', row['file_number'], row['name'], ''])
            for row in section.get('tcp_not_in_adp', []):
                out.writerow([region, 'TCP hours, no active ADP match', row['geid'], row['name'], row['store']])
    # Counts only in the log; names and numbers stay in the private artifact.
    print(f'ADP workers read: {len(workers)} assignments')
    for region in REGION_COMPANY:
        s = report[region]
        print(f"{region}: ADP active {s['adp_active']} of {s['adp_workers']}"
              + (f"; EPI rows {s['epi_rows']}; missing from EPI {len(s['missing_from_epi'])}; EPI not active {len(s['epi_not_active_in_adp'])}" if 'epi_rows' in s else '; no EPI template')
              + (f"; TCP people {s['tcp_people']}; TCP not in ADP {len(s['tcp_not_in_adp'])}" if 'tcp_people' in s else ''))
    if report['other_company_codes']:
        print('Other ADP company codes seen: ' + ', '.join(report['other_company_codes']))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ADPError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
