"""Reconcile a native Complete Payroll XML export against TCP API shifts.

This is a manual fallback, not an API repair. It never edits TCP timecards.
Only zero calculated hours can be replaced; existing nonzero API hours must
agree. All worked shifts within the export's dates must match exactly.
"""
import argparse
import copy
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path

NS = '{urn:schemas-microsoft-com:office:spreadsheet}'
PERIOD = re.compile(r'For the period of (\d+/\d+/\d{4}) to (\d+/\d+/\d{4})')
PERSON = re.compile(r'Name: (.*?)\s+Number: (\d+)(?:\s+Export Code: (\S+))?')
DAYS = {'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'}
HOUR_KEYS = ('regular', 'overtime1', 'overtime2')


def duration(value):
    if not re.fullmatch(r'\d+:\d{2}', value) or int(value.split(':')[1]) >= 60:
        raise ValueError('Invalid native report duration')
    h, m = map(int, value.split(':'))
    return h * 60 + m


def rows(root):
    for row in root.iter(NS + 'Row'):
        result = []
        for cell in row.findall(NS + 'Cell'):
            index = int(cell.get(NS + 'Index', len(result) + 1)) - 1
            merge = int(cell.get(NS + 'MergeAcross', 0))
            if index < len(result) or index > 100 or not 0 <= merge <= 100:
                raise ValueError('Unsupported XML cell layout')
            result.extend([''] * (index - len(result)))
            data = cell.find(NS + 'Data')
            result.append(''.join(data.itertext()).strip() if data is not None else '')
            result.extend([''] * merge)
        yield result


def punch(day, clock, first, last):
    candidates = []
    for year in range(first.year, last.year + 1):
        try:
            value = datetime.strptime(f'{day}/{year} {clock}', '%m/%d/%Y %I:%M %p')
        except ValueError:
            continue
        if first <= value.date() <= last:
            candidates.append(value)
    if len(candidates) != 1:
        raise ValueError('Missing, open, or ambiguous punch in native report')
    return candidates[0].isoformat(timespec='seconds')


def read_export(path):
    raw = Path(path).read_bytes()
    if len(raw) > 25_000_000 or b'\x00' in raw or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Unsupported XML document')
    # TCP exports UTF-8 XML; reject alternate encodings that could hide entities.
    text = raw.decode('utf-8-sig')
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('Unsupported XML document')
    root = ET.fromstring(text)
    if root.tag != NS + 'Workbook':
        raise ValueError('Expected TCP Complete Payroll SpreadsheetML XML')
    period = None
    employee = None
    columns = None
    shifts = {}
    title = False
    for row in rows(root):
        if not row:
            continue
        title |= row[0] == 'Complete Payroll'
        found = PERIOD.fullmatch(row[0])
        if found:
            current = tuple(datetime.strptime(v, '%m/%d/%Y').date() for v in found.groups())
            if period and current != period:
                raise ValueError('Mixed periods in native report')
            period = current
            employee = columns = None
            continue
        found = PERSON.fullmatch(row[0])
        if found:
            employee = found.group(2)
            columns = None
            continue
        if row[0] == 'Week':
            if row[:8] != ['Week', '', 'D', 'In', 'In', 'Out', 'Out', 'Job Code']:
                raise ValueError('Unsupported native shift header')
            required = ['Hours', 'Reg', 'Ovt1', 'Ovt2', 'Comp Reg', 'Comp OT1', 'Comp OT2']
            if any(row.count(key) != 1 for key in required):
                raise ValueError('Missing or duplicate calculated-hours columns')
            columns = {key: row.index(key) for key in required}
            continue
        if len(row) < 3 or row[2] not in DAYS:
            continue
        if not period or not employee or not columns or len(row) <= max(columns.values()):
            raise ValueError('Shift outside a recognized employee table')
        first, last = period
        if last < first or (last - first).days > 89:
            raise ValueError('Invalid export period')
        time_in = punch(row[3], row[4], first, last)
        time_out = punch(row[5], row[6], date.fromisoformat(time_in[:10]), last + timedelta(days=1))
        if time_out < time_in or not row[7].isdigit():
            raise ValueError('Invalid native shift punch or job code')
        values = tuple(duration(row[columns[key]]) for key in ['Reg', 'Ovt1', 'Ovt2'])
        if any(duration(row[columns[key]]) for key in ['Comp Reg', 'Comp OT1', 'Comp OT2']):
            raise ValueError('Comp time requires a separately verified mapping')
        if sum(values) != duration(row[columns['Hours']]) or (not sum(values) and time_out != time_in) or (sum(values) and time_out == time_in):
            raise ValueError('Native worked hours are zero or do not reconcile')
        key = (employee, row[7], time_in, time_out)
        if key in shifts:
            raise ValueError('Duplicate native shift')
        shifts[key] = values
    if not title or not period or not shifts:
        raise ValueError('No complete native payroll shifts found')
    return {'period': period, 'shifts': shifts, 'sha256': hashlib.sha256(raw).hexdigest()}


def apply_export(report, path):
    native = read_export(path)
    first, last = native['period']
    if not report['period']['startDate'] <= first.isoformat() <= last.isoformat() <= report['period']['stopDate']:
        raise ValueError('Export dates fall outside the API payroll period')
    if report.get('errors') or any(not report['datasets'].get(k, {}).get('complete') for k in ('hours', 'worksegments', 'jobcodes')):
        raise ValueError('A complete, error-free API inspection is required')
    result = copy.deepcopy(report)
    jobs = {str(j['jobCodeId']): j for j in result['datasets']['jobcodes']['records']}
    expected = {}
    ids = set()
    for person in result['datasets']['hours']['records']:
        for segment in person['segments']:
            if not first.isoformat() <= (segment.get('timeIn') or '')[:10] <= last.isoformat():
                continue
            job_id = str(segment.get('jobCode', {}).get('jobCodeId', ''))
            if job_id not in jobs:
                raise ValueError('Unknown API job code')
            if segment.get('isLeave') or jobs[job_id].get('leaveCode'):
                continue
            key = (str(person['employee']['employeeId']), job_id, segment['timeIn'], segment.get('timeOut'))
            if key in expected or str(segment['recordId']) in ids:
                raise ValueError('Duplicate API shift')
            expected[key] = segment
            ids.add(str(segment['recordId']))
    work_ids = {str(s['id']) for s in result['datasets']['worksegments']['records']
                if first.isoformat() <= (s.get('timeIn') or '')[:10] <= last.isoformat()
                and str(s.get('jobCodeId')) in jobs and not jobs[str(s['jobCodeId'])].get('leaveCode')}
    if ids != work_ids or set(expected) != set(native['shifts']):
        raise ValueError(f'Export/API coverage or punches differ: {len(native["shifts"])} native shifts, {len(expected)} calculated shifts, {len(work_ids)} work shifts')
    changed = []
    for key, segment in expected.items():
        values = native['shifts'][key]
        before = tuple(duration(segment['hours'][k]) for k in HOUR_KEYS)
        if sum(before) and before != values:
            raise ValueError('Nonzero API hours disagree with native hours; review required')
        if before != values:
            changed.append({'record_id': str(segment['recordId']), 'before': dict(segment['hours']),
                            'native_minutes': dict(zip(HOUR_KEYS, values))})
            segment['hours'].update({k: f'{v // 60}:{v % 60:02d}' for k, v in zip(HOUR_KEYS, values)})
    result.setdefault('native_export_reconciliation', []).append({
        'source_sha256': native['sha256'], 'source_file': Path(path).name,
        'start_date': first.isoformat(), 'end_date': last.isoformat(),
        'matched_shifts': len(expected), 'replacements': changed,
        'method': 'Exact GEID, job code and both punch times; native calculated regular/OT, no elapsed-time inference'})
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audit', type=Path)
    parser.add_argument('native_xml', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--gateway-input', type=Path,
                        help='Same-period verified Gateway JSON; also attempts a private workbook/email preview')
    args = parser.parse_args()
    if args.output.resolve() in (args.audit.resolve(), args.native_xml.resolve()):
        parser.error('Output must not overwrite either source')
    merged = apply_export(json.loads(args.audit.read_text(encoding='utf-8')), args.native_xml)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(merged, indent=2), encoding='utf-8')
    evidence = merged['native_export_reconciliation'][-1]
    print(f'Matched {evidence["matched_shifts"]} shifts; restored {len(evidence["replacements"])} calculated splits. Remaining payroll checks still apply. No email sent.')
    if args.gateway_input:
        from datetime import timezone
        from payroll_run import normalize, period_for, prepare, write_issues
        period_for(datetime.now(timezone.utc), merged['period']['startDate'], merged['period']['stopDate'])
        payroll = normalize(merged)
        preview = args.output.parent / 'native-preview'
        preview.mkdir(parents=True, exist_ok=True)
        write_issues(merged, payroll, preview)
        (preview / 'readiness.json').write_text(json.dumps({
            'period': payroll['period'], 'tcp_ready': payroll['verified'], 'issues': payroll['issues'],
            'preview_complete': False}, indent=2), encoding='utf-8')
        if payroll['issues']:
            raise SystemExit('Preview blocked by remaining TCP checks; see private readiness.json and corrections CSV. No email sent.')
        prepare(json.loads(args.gateway_input.read_text(encoding='utf-8')), payroll, preview)
        (preview / 'readiness.json').write_text(json.dumps({
            'period': payroll['period'], 'tcp_ready': True, 'issues': [],
            'preview_complete': True, 'email_sent': False}, indent=2), encoding='utf-8')
        print('Prepared private workbooks and email preview; no email sent.')
