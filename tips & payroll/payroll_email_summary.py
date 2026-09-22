"""Combine verified store workbooks by GEID and prepare Alicia's payroll email.

This module prepares an email; it does not send it or change any ADP payroll.
"""
import argparse
import csv
import hashlib
import html
import io
import json
import re
from collections import defaultdict
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path

from openpyxl import load_workbook
from allocate_tips import MANAGEMENT, cents, decimal_text, period, store_id


ROLE_NAMES = {
    'unassigned': 'unassigned',
    'crew': 'crew', 'crew member': 'crew member', 'crew leader': 'crew leader',
    'manager': 'manager', 'mgr': 'manager', 'general manager': 'manager',
    'co-manager': 'co-manager', 'co manager': 'co-manager', 'co mgr': 'co-manager', 'comanager': 'co-manager',
    'assistant manager': 'assistant manager', 'assistant mgr': 'assistant manager',
    'asst manager': 'assistant manager', 'asst mgr': 'assistant manager',
}
LABELS = {'crew': 'CREW', 'crew member': 'CREW', 'crew leader': 'CREW LEADER', 'manager': 'MANAGER',
          'co-manager': 'CO-MANAGER', 'assistant manager': 'ASST MGR', 'unassigned': 'UNASSIGNED'}
FIELDS = ['GEID', 'Payroll ID', 'First Name', 'Last Name', 'Job Title', 'Total Hours', 'Tips', 'Reg Hours', 'OT Hours']
ALIASES = {'regular hours': 'Reg Hours', 'reg hours': 'Reg Hours', 'overtime hours': 'OT Hours', 'ot hours': 'OT Hours'}


def roles(value):
    if isinstance(value, str):
        value = re.split(r'[;/]', value)
    if not isinstance(value, list) or not value:
        raise ValueError('Every employee needs a verified role classification')
    result = set()
    for title in value:
        normalized = re.sub(r'\s+', ' ', str(title).strip().lower().replace('.', ''))
        if normalized not in ROLE_NAMES:
            raise ValueError(f'Unclassified role: {title}')
        result.add(ROLE_NAMES[normalized])
    return result


def identity(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('Missing GEID')
    if isinstance(value, (int, float)):
        if not Decimal(str(value)).is_finite() or Decimal(str(value)) != int(value):
            raise ValueError('Employee identifiers must be whole numbers or text')
        value = str(int(value))
    result = str(value).strip()
    if not result:
        raise ValueError('Missing GEID')
    return result


def name_key(first, last):
    return tuple(' '.join(str(value).split()).casefold() for value in (first, last))


def summarize_rows(manifest, source_rows):
    dates = period(manifest)
    for field in ('verified', 'roster_verified', 'cross_store_ot_verified'):
        if manifest.get(field) is not True:
            raise ValueError(f'{field} must be true before preparing the payroll email')
    if manifest.get('issues'):
        raise ValueError('Resolve payroll input issues before preparing the email')
    expected = [store_id(s) for s in manifest.get('expected_stores', [])]
    if not expected or len(set(expected)) != len(expected):
        raise ValueError('Expected stores must be an explicit, unique list')
    pools = {store_id(k): cents(v, 'verified Gateway store tips') for k, v in manifest.get('gateway_tips', {}).items()}
    if set(pools) != set(expected) or len(pools) != len(manifest['gateway_tips']):
        raise ValueError('Verified Gateway totals must cover every expected store exactly once')
    people = {}
    for person in manifest.get('roster', []):
        geid = identity(person['geid'])
        if geid in people:
            raise ValueError(f'Duplicate roster GEID: {geid}')
        if not str(person.get('first_name', '')).strip() or not str(person.get('last_name', '')).strip():
            raise ValueError(f'Missing roster name for GEID {geid}')
        people[geid] = {
            'geid': geid, 'first_name': str(person['first_name']), 'last_name': str(person['last_name']),
            'roles': roles(person['roles']), 'payroll_ids': set(str(p) for p in person.get('payroll_ids', []) if str(p).strip()),
            'stores': set(), 'regular': 0, 'overtime': 0, 'tips': 0,
        }
    if not people:
        raise ValueError('Supply the complete current payroll roster, including zero-hour employees')
    seen, store_totals, detail = set(), defaultdict(lambda: {'regular': 0, 'overtime': 0, 'tips': 0}), []
    for source in source_rows:
        sid, geid = store_id(source['store']), identity(source['geid'])
        if sid not in expected or geid not in people:
            raise ValueError('Workbook store/GEID is not in the verified period roster')
        if (sid, geid) in seen:
            raise ValueError(f'Duplicate employee row in store {sid}, GEID {geid}')
        seen.add((sid, geid))
        person = people[geid]
        if name_key(source['first_name'], source['last_name']) != name_key(person['first_name'], person['last_name']):
            raise ValueError(f'Name/GEID mismatch in store {sid}, GEID {geid}')
        person['roles'].update(roles(source['roles']))
        regular = cents(source['regular_hours'], 'regular decimal hours')
        overtime = cents(source['overtime_hours'], 'overtime decimal hours')
        total = cents(source['total_hours'], 'total decimal hours')
        tips = cents(source['tips'], 'employee tips')
        if regular + overtime != total:
            raise ValueError(f'Regular + OT does not equal total hours: {sid}, GEID {geid}')
        person['stores'].add(sid)
        if source.get('payroll_id'):
            person['payroll_ids'].add(str(source['payroll_id']))
        for key, value in (('regular', regular), ('overtime', overtime), ('tips', tips)):
            person[key] += value
            store_totals[sid][key] += value
        detail.append({'store': sid, 'geid': geid, 'regular_hours': decimal_text(regular),
                       'overtime_hours': decimal_text(overtime), 'tips': decimal_text(tips)})
    for sid in expected:
        if store_totals[sid]['tips'] != pools[sid]:
            raise ValueError(f'Workbook employee tips do not reconcile to Gateway for store {sid}')
    result = []
    for person in sorted(people.values(), key=lambda p: (p['last_name'].casefold(), p['first_name'].casefold(), p['geid'])):
        management = bool(person['roles'] & MANAGEMENT)
        if 'unassigned' in person['roles'] and (person['regular'] or person['overtime'] or person['tips']):
            raise ValueError(f'Unassigned employee has hours or tips: GEID {person["geid"]}')
        if management and person['tips']:
            raise ValueError(f'Manager received tips under one or more stores: GEID {person["geid"]}')
        result.append({
            'geid': person['geid'], 'payroll_ids': sorted(person['payroll_ids']),
            'first_name': person['first_name'], 'last_name': person['last_name'],
            'job_title': ' / '.join(sorted({LABELS[r] for r in person['roles']})),
            'management': management, 'stores': sorted(person['stores']),
            'regular_hours': decimal_text(person['regular']), 'overtime_hours': decimal_text(person['overtime']),
            'total_hours': decimal_text(person['regular'] + person['overtime']), 'tips': decimal_text(person['tips']),
        })
    total = {field: decimal_text(sum(cents(row[field], field) for row in result))
             for field in ('regular_hours', 'overtime_hours', 'total_hours', 'tips')}
    return {'period': dates, 'employees': result, 'managers': [r for r in result if r['management']], 'warnings': manifest.get('warnings', []),
            'totals': total, 'source_rows': detail, 'stores': expected}


def date_label(dates):
    def short(value):
        year, month, day = value.split('-')
        return f'{int(month)}.{int(day)}.{year[-2:]}'
    return f'{short(dates["start_date"])}-{short(dates["end_date"])}'


def read_store_workbooks(manifest, directory):
    dates = period(manifest)
    expected = {store_id(s) for s in manifest['expected_stores']}
    specs = manifest.get('workbooks', [])
    if {store_id(spec['store']) for spec in specs} != expected or len(specs) != len(expected):
        raise ValueError('Provide exactly one workbook for every expected store')
    rows, sources, paths = [], [], set()
    for spec in specs:
        sid = store_id(spec['store'])
        file = (directory / spec['file']).resolve()
        if file in paths:
            raise ValueError('The same workbook cannot be counted for multiple stores')
        paths.add(file)
        if file.name != f'{sid} {date_label(dates)}.xlsx':
            raise ValueError(f'Workbook filename must identify its store and matching pay period: {sid}')
        if not isinstance(spec.get('sheet'), str) or not spec['sheet']:
            raise ValueError('Specify the payroll sheet explicitly to avoid counting summary sheets twice')
        values = load_workbook(file, read_only=True, data_only=True, keep_links=False)
        try:
            sheet = values[spec['sheet']]
            header_row = spec.get('header_row', 1)
            if not isinstance(header_row, int) or isinstance(header_row, bool) or header_row < 1:
                raise ValueError('header_row must be a positive integer')
            iterator = sheet.iter_rows(min_row=header_row)
            header = next(iterator, [])
            names = [ALIASES.get(str(c.value).strip().lower(), str(c.value).strip()) for c in header]
            for field in FIELDS:
                if names.count(field) != 1:
                    raise ValueError(f'Expected one {field} column in {file.name}')
            indexes = {field: names.index(field) for field in FIELDS}
            for number, cells in enumerate(iterator, header_row + 1):
                def get(field):
                    cell = cells[indexes[field]]
                    if cell.data_type == 'e':
                        raise ValueError(f'Excel error in {file.name}, row {number}, {field}')
                    return cell.value
                geid = get('GEID')
                if geid is None or str(geid).strip() == '':
                    # Skip spacing/subtotal rows; a name without an identity is an error.
                    if any(get(f) not in (None, '') for f in ('First Name', 'Last Name', 'Payroll ID', 'Job Title')):
                        raise ValueError(f'Employee row is missing GEID in {file.name}, row {number}')
                    continue
                payroll_cell = cells[indexes['Payroll ID']]
                payroll_id = get('Payroll ID')
                if payroll_id not in (None, ''):
                    payroll_id = identity(payroll_id)
                    if re.fullmatch('0+', payroll_cell.number_format or ''):
                        payroll_id = payroll_id.zfill(len(payroll_cell.number_format))
                rows.append({'store': sid, 'geid': identity(geid), 'payroll_id': payroll_id,
                             'first_name': get('First Name'), 'last_name': get('Last Name'),
                             'roles': get('Job Title'), 'regular_hours': get('Reg Hours'),
                             'overtime_hours': get('OT Hours'), 'total_hours': get('Total Hours'), 'tips': get('Tips')})
        finally:
            values.close()
        sources.append({'path': str(file), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
    return rows, sources


COLUMNS = [('GEID', 'geid'), ('Payroll ID', 'payroll_ids'), ('First Name', 'first_name'),
           ('Last Name', 'last_name'), ('Job Title', 'job_title'), ('Stores worked', 'stores'),
           ('Regular Hours', 'regular_hours'), ('Overtime Hours', 'overtime_hours'),
           ('Total Hours', 'total_hours'), ('Tips', 'tips')]


def display(row, key):
    if key == 'name':
        return f'{row["first_name"]} {row["last_name"]}'
    value = row[key]
    return ', '.join(value) if isinstance(value, list) else str(value)


def csv_text(rows):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow([label for label, _ in COLUMNS])
    for row in rows:
        # Keep arbitrary names/titles from being interpreted as spreadsheet formulas.
        writer.writerow([("'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value)
                         for _, key in COLUMNS for value in [display(row, key)]])
    return output.getvalue()


def table(rows, include_stores=False):
    # Compact markup keeps large rosters readable without per-cell CSS bloat.
    columns = [('GEID', 'geid'), ('Name', 'name'), ('Job Title', 'job_title')]
    if include_stores:
        columns.append(('Stores worked', 'stores'))
    columns += COLUMNS[-4:]
    head = ''.join(f'<th>{label}</th>' for label, _ in columns)
    body = []
    for row in rows:
        cells = ''.join(f'<td>{html.escape(display(row, key))}</td>' for _, key in columns)
        body.append(f'<tr>{cells}</tr>')
    return f'<table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:12px"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def build_message(summary, sources):
    label = date_label(summary['period'])
    message = EmailMessage()
    message['From'] = 'ab@caazllc.com'
    message['To'] = 'alicia@lccollc.com'
    message['Subject'] = f'Payroll {label} — combined employee hours and tips'
    note = ('Hours are decimal hours. Each person appears once in the full list, with hours and tips combined across stores. '
            'Managers receive no tips. Overtime is the verified TCP classification; it is not recalculated from the two-week total.')
    if summary.get('warnings'):
        note += ' Setup notes: ' + '. '.join(summary['warnings']) + '.'
    message.set_content(f'Payroll {label}\n\n{note}\n\nManagers across stores\n{csv_text(summary["managers"])}\nAll employees and managers\n{csv_text(summary["employees"])}')
    totals = summary['totals']
    total_line = (f'Regular hours: {totals["regular_hours"]}; overtime hours: {totals["overtime_hours"]}; '
                  f'total hours: {totals["total_hours"]}; tips: ${totals["tips"]}.')
    message.add_alternative(
        f'<html><body><h2>Payroll {label}</h2><p>{note}</p><p>{total_line}</p>'
        f'<h3>Managers — combined across stores</h3>{table(summary["managers"], include_stores=True)}'
        f'<h3>All employees and managers</h3>{table(summary["employees"])}'
        '<p>The manager rows above are also included in the full list. They are counted once in the totals.</p></body></html>', subtype='html')
    for name, rows in (('Combined payroll', summary['employees']), ('Combined managers', summary['managers'])):
        message.add_attachment(csv_text(rows).encode('utf-8-sig'), maintype='text', subtype='csv', filename=f'{name} {label}.csv')
    size = 0
    for source in sources:
        file = Path(source['path'])
        data = file.read_bytes()
        if hashlib.sha256(data).hexdigest() != source['sha256']:
            raise ValueError('A store workbook changed after its summary was prepared')
        size += len(data)
        if size > 17 * 1024 * 1024:
            raise ValueError('Store attachments exceed the email size budget; prepare smaller verified workbooks')
        message.add_attachment(data, maintype='application', subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename=file.name)
    return message


def prepare_email(manifest_path, output):
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    rows, sources = read_store_workbooks(manifest, manifest_path.parent)
    summary = summarize_rows(manifest, rows)
    message = build_message(summary, sources)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'combined-payroll.json').write_text(json.dumps({**summary, 'sources': sources}, indent=2), encoding='utf-8')
    (output / 'payroll-email.eml').write_bytes(message.as_bytes())
    (output / 'payroll-email.html').write_text(message.get_body(preferencelist=('html',)).get_content(), encoding='utf-8')
    for name, people in (('combined-payroll', summary['employees']), ('combined-managers', summary['managers'])):
        (output / f'{name}.csv').write_text(csv_text(people), encoding='utf-8-sig')
    print(f'Prepared email for {len(summary["employees"])} people, including {len(summary["managers"])} managers. No email sent.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    prepare_email(args.manifest, args.output)
