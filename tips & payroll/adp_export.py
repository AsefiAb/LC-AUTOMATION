"""Fill original regional ADP CSV exports from verified payroll and Gateway data.

Preserve control rows and employee identity/rate fields. This module prepares
files only; it never logs into ADP, changes a roster, sends mail or submits pay.
"""
import argparse
import csv
import hashlib
import io
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from allocate_tips import allocate, cents, decimal_text, period
from gateway import REGIONS

HEADER = ['!CO CODE', 'BATCH ID', 'FILE #', 'BATCH DESCRIPTION', 'Name',
          'Rate', 'Reg Hours', 'O/T Hours', 'TIP']
CONTROL = {
    '!Column7': ['!Column7', 'Reg Hours', '', 'Reg Hours'],
    '!Column8': ['!Column8', 'O/T Hours', '', 'O/T Hours'],
    '!Column9': ['!Column9', 'TIP', '', 'Earnings 3 Code', 'Earnings 3 Amount', 'TIP'],
}


def read_template(raw, company, batch):
    encoding = 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        encoding = 'cp1252'
        text = raw.decode(encoding)
    physical = text.splitlines(keepends=True)
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    parts, employees, controls = [], {}, {}
    header_count, previous = 0, 0
    for row in reader:
        original = ''.join(physical[previous:reader.line_num])
        previous = reader.line_num
        key = None
        if row and row[:8] == HEADER[:8] and len(row) == 9 and row[8] in ('TIP', 'Other Earnings - TIP EARNS'):
            header_count += 1
            tip_header = row[8]
        elif row and row[0] in CONTROL:
            expected = CONTROL[row[0]]
            if row[0] == '!Column9' and row[1:2] == ['Other Earnings - TIP EARNS']:
                expected = ['!Column9', 'Other Earnings - TIP EARNS', '', 'Earnings 3 Code', 'Earnings 3 Amount', 'T']
            if row[0] in controls or row[:len(expected)] != expected:
                raise ValueError('ADP earnings-column metadata is duplicated or unsupported')
            controls[row[0]] = row
        elif row and row[0] and not row[0].startswith('!'):
            if len(row) != 9 or row[0] != company or row[1] != batch or not row[2] or not row[4]:
                raise ValueError('ADP template has a wrong company/batch or incomplete employee identity')
            key = (row[0], row[2])  # IDs remain text, preserving leading zeros.
            if key in employees:
                raise ValueError('Duplicate ADP company/file number')
            employees[key] = row
        elif row and any(row) and not row[0].startswith('!'):
            raise ValueError('Unrecognized ADP template row')
        parts.append((key, row, original))
    if header_count != 1 or set(controls) != set(CONTROL) or not employees:
        raise ValueError('ADP template is missing its header, earnings metadata or employee roster')
    if controls['!Column9'][1] != tip_header:
        raise ValueError('ADP tip header and earnings metadata do not agree')
    return {'parts': parts, 'employees': employees, 'encoding': encoding,
            'newline': '\r\n' if '\r\n' in text else '\n'}


def build_exports(payroll, gateway, mapping, templates):
    dates = period(payroll)
    if period(mapping) != dates or mapping.get('verified') is not True or mapping.get('issues'):
        raise ValueError('Verified current-period ADP identity mappings are required')
    # Recompute tips from verified source inputs, never trust edited tip totals.
    allocation = allocate(gateway, payroll)
    expected_stores = {f'{prefix}-{i:02d}': region for region, (prefix, count) in REGIONS.items()
                       for i in range(1, count + 1)}
    if {s['store'] for s in allocation['stores']} != set(expected_stores):
        raise ValueError('Gateway must include all 18 stores')
    if set(templates) != set(REGIONS) or set(mapping.get('templates', {})) != set(REGIONS):
        raise ValueError('Current ADP exports for CO, AZ and TX are required')
    roster = {r['geid'] for r in payroll['roster']}
    if len(roster) != len(payroll['roster']) or roster != {r['employee_id'] for r in payroll['employees']}:
        raise ValueError('Payroll roster and employee hours do not cover exactly the same people')
    tips = {(r['store'], r['employee_id']): cents(r['tips'], 'tips') for r in allocation['employees']}
    totals = defaultdict(lambda: [0, 0, 0])
    for row in payroll['employees']:
        regular, overtime = cents(row['regular_hours'], 'regular hours'), cents(row['overtime_hours'], 'overtime hours')
        if regular + overtime != cents(row['total_hours'], 'total hours'):
            raise ValueError('Regular and overtime hours do not add to total hours')
        values = totals[(expected_stores[row['store']], row['employee_id'])]
        for i, value in enumerate((regular, overtime, tips[(row['store'], row['employee_id'])])):
            values[i] += value
    identities, targets = {}, set()
    for row in mapping.get('employees', []):
        key = (row['region'], row['geid'])
        target = (row['company_code'], row['file_number'])
        if not all(isinstance(x, str) and x for x in (*key, *target)):
            raise ValueError('ADP mappings require nonempty text identifiers')
        if key in identities or target in targets:
            raise ValueError('Duplicate payroll mapping or ADP target')
        identities[key] = target
        targets.add(target)
    if set(identities) != set(totals):
        raise ValueError('Every employee/region, including zero-hour staff, needs exactly one ADP mapping')
    exports, audit = {}, {'period': dates, 'submitted': False, 'regions': {}}
    for region in REGIONS:
        spec, raw = mapping['templates'][region], templates[region]
        if spec.get('sha256') != hashlib.sha256(raw).hexdigest():
            raise ValueError('ADP template changed after its identity mapping was verified')
        template = read_template(raw, spec['company_code'], spec['batch_id'])
        values = {identities[key]: value for key, value in totals.items() if key[0] == region}
        if set(values) != set(template['employees']):
            raise ValueError(f'{region}: ADP roster differs from mapped TCP payroll; no employee may be silently omitted')
        output = []
        for key, row, original in template['parts']:
            if key is None:
                output.append(original)  # Preserve every ! row byte-for-byte after encoding.
                continue
            buffer = io.StringIO(newline='')
            csv.writer(buffer, lineterminator=template['newline']).writerow(row[:6] + [decimal_text(v) for v in values[key]])
            output.append(buffer.getvalue())
        content = ''.join(output).encode(template['encoding'])
        # Reparse to verify retained identity/rate columns and exact monetary/hour totals.
        saved = read_template(content, spec['company_code'], spec['batch_id'])
        for key, row in saved['employees'].items():
            if row[:6] != template['employees'][key][:6] or [cents(v, 'export value') for v in row[6:]] != values[key]:
                raise ValueError('ADP output did not preserve employee identity/rate or calculated values')
        exports[region] = content
        audit['regions'][region] = {
            'company_code': spec['company_code'], 'batch_id': spec['batch_id'],
            'employees': len(values), 'source_sha256': spec['sha256'],
            'output_sha256': hashlib.sha256(content).hexdigest(),
            'regular_hours': decimal_text(sum(v[0] for v in values.values())),
            'overtime_hours': decimal_text(sum(v[1] for v in values.values())),
            'tips': decimal_text(sum(v[2] for v in values.values())),
        }
    return exports, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('payroll', type=Path)
    parser.add_argument('gateway', type=Path)
    parser.add_argument('mapping', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    read = lambda p: json.loads(p.read_text(encoding='utf-8-sig'))
    payroll, gateway, mapping = read(args.payroll), read(args.gateway), read(args.mapping)
    from payroll_run import period_for
    dates = period(payroll)
    period_for(datetime.now(timezone.utc), dates['start_date'], dates['end_date'])
    paths = {region: (args.mapping.parent / spec['file']).resolve() for region, spec in mapping['templates'].items()}
    exports, audit = build_exports(payroll, gateway, mapping, {r: p.read_bytes() for r, p in paths.items()})
    # Validate every region before writing any output. Never overwrite an earlier batch.
    if args.output.exists():
        raise ValueError('Use a new output directory to preserve previous payroll batches')
    args.output.mkdir(parents=True)
    for region, content in exports.items():
        folder = args.output / region
        folder.mkdir()
        (folder / paths[region].name).write_bytes(content)
    (args.output / 'adp-export-audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    print('Prepared three regional ADP CSVs. No email sent and no ADP import submitted.')


if __name__ == '__main__':
    main()
