"""Validate a locally reviewed payroll package, then deliver its regional emails.

No credentials or private payroll files belong in Git. The package is staged on
the trusted Windows runner; source reconciliation is repeated before any email.
"""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook
from allocate_tips import allocate
from payroll_run import normalize, period_for, STORES
from payroll_email_summary import FIELDS, LABELS, date_label, regional_people, summarize_rows, roles
from regional_email import build_region_messages, regional_filename, send_regions
from tcp_report_fallback import apply_export


def verify_workbook(path, expected, sheet_name):
    """Read cached Excel results and check every identity, classification and cent."""
    book = load_workbook(path, data_only=True, read_only=True)
    try:
        if book.sheetnames != [sheet_name]:
            raise ValueError(f'Expected one worksheet in {path.name}')
        sheet = book.active
        values = list(sheet.iter_rows(values_only=True))
        headers = [i for i, row in enumerate(values[:10]) if list(row[:9]) == FIELDS]
        if len(headers) != 1:
            raise ValueError(f'Payroll headers not found in {path.name}')
        actual = {}
        totals = None
        for row in values[headers[0] + 1:]:
            if str(row[0]).upper() == 'TOTAL':
                totals = row
                break
            if row[0] is None:
                continue
            geid = str(row[0])
            if geid in actual:
                raise ValueError(f'Duplicate employee in {path.name}')
            actual[geid] = row
        if set(actual) != {p['geid'] for p in expected}:
            raise ValueError(f'Employee coverage mismatch in {path.name}')
        numeric = [('total_hours', 5), ('tips', 6), ('regular_hours', 7), ('overtime_hours', 8)]
        for person in expected:
            row = actual[person['geid']]
            if tuple(row[2:4]) != (person['first_name'], person['last_name']) or roles(row[4]) != roles(person['job_title']):
                raise ValueError(f'Employee name or role mismatch in {path.name}')
            for key, column in numeric:
                if row[column] is None or abs(Decimal(str(row[column])) - Decimal(person[key])) > Decimal('0.0000001'):
                    raise ValueError(f'Incorrect {key} in {path.name}')
        if totals is None:
            raise ValueError(f'Missing totals in {path.name}')
        for key, column in numeric:
            expected_total = sum(Decimal(p[key]) for p in expected)
            if totals[column] is None or abs(Decimal(str(totals[column])) - expected_total) > Decimal('0.0000001'):
                raise ValueError(f'Incorrect total {key} in {path.name}')
    finally:
        book.close()


def prepare_package(folder, dates):
    audit = json.loads((folder / 'tcp-audit.json').read_text(encoding='utf-8'))
    audit = apply_export(audit, folder / 'native-payroll.xml')
    payroll = normalize(audit)
    if payroll['period'] != dates or not payroll['verified'] or payroll['issues']:
        raise ValueError('TCP payroll is incomplete or has the wrong period')
    gateway = json.loads((folder / 'gateway-input.json').read_text(encoding='utf-8'))
    allocation = allocate(gateway, payroll)
    if {s['store'] for s in allocation['stores']} != set(STORES):
        raise ValueError('All 18 Gateway stores must be verified')
    manifest = {'period': dates, 'verified': True, 'roster_verified': True, 'cross_store_ot_verified': True,
                'issues': [], 'expected_stores': STORES, 'roster': payroll['roster'],
                'warnings': payroll.get('warnings', []),
                'gateway_tips': {s['store']: s['gateway_tips'] for s in allocation['stores']}}
    people = {p['geid']: p for p in payroll['roster']}
    hours = {(p['store'], p['employee_id']): p for p in payroll['employees']}
    rows = []
    for entry in allocation['employees']:
        person = people[entry['employee_id']]
        h = hours[(entry['store'], entry['employee_id'])]
        rows.append({'store': entry['store'], 'geid': entry['employee_id'], 'first_name': person['first_name'],
                     'last_name': person['last_name'], 'roles': entry['roles'], 'payroll_id': '',
                     'regular_hours': h['regular_hours'], 'overtime_hours': h['overtime_hours'],
                     'total_hours': h['total_hours'], 'tips': entry['tips'],
                     'job_title': '; '.join(sorted({LABELS[r] for r in entry['roles']}))})
    summary = summarize_rows(manifest, rows)
    expected_files = []
    for sid in STORES:
        path = folder / f'{sid} {date_label(dates)}.xlsx'
        verify_workbook(path, [p for p in rows if p['store'] == sid], 'Payroll')
        expected_files.append(path)
    for region, persons in regional_people(summary).items():
        path = folder / regional_filename(region, dates)
        verify_workbook(path, persons, region)
        expected_files.append(path)
    sources = [{'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in expected_files]
    messages = build_region_messages(summary, sources)
    for message in messages:
        (folder / (message['Subject'] + '.eml')).write_bytes(message.as_bytes())
    report = {'period': dates, 'totals': summary['totals'], 'files': sources,
              'emails': [{'subject': m['Subject'], 'to': m['To'],
                          'attachments': [a.get_filename() for a in m.iter_attachments()]} for m in messages]}
    (folder / 'verified-package.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('Verified all 21 workbooks against reconciled TCP hours and store-specific Gateway allocations.', flush=True)
    return messages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--folder', type=Path, help='Verified package directory (cloud or local)')
    args = parser.parse_args()
    dates = period_for(datetime.now(timezone.utc), args.start, args.end)
    folder = args.folder or (Path(os.environ['LOCALAPPDATA']) / 'LC-Automation' / 'payroll-delivery' / (args.start + '_' + args.end))
    messages = prepare_package(folder, dates)
    if args.send:
        if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('GITHUB_REPOSITORY') != 'AsefiAb/LC-AUTOMATION':
            raise ValueError('Sending requires the private repository workflow')
        results = send_regions(messages, args.end)
        (folder / 'delivery-results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    else:
        print('Verification only; no email sent.')


if __name__ == '__main__':
    main()
