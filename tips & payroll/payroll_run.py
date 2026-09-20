"""Gateway + TCP -> per-store decimal-hour workbooks -> consolidated payroll email.

ADP is deliberately outside this pipeline. Preflight and preview never send mail.
"""
import argparse
import csv
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from allocate_tips import allocate, MANAGEMENT
import gateway
from payroll_email_summary import (roles, LABELS, FIELDS, date_label, read_store_workbooks,
                                   summarize_rows, build_message, COLUMNS, display)
from summarize_inspection import summarize, store_label
from tcp_inspect import TCP

FOLDER = Path(__file__).resolve().parent
STORES = [f'{franchise}-{i:02d}' for franchise, count in gateway.REGIONS.values() for i in range(1, count + 1)]
ANCHOR = date(2026, 9, 22)


def period_for(now, start=None, end=None):
    local = now.astimezone(ZoneInfo('America/Los_Angeles'))
    if start or end:
        first, last = date.fromisoformat(start or ''), date.fromisoformat(end or '')
        if (last - first).days != 13 or last >= local.date():
            raise ValueError('Use a completed 14-day period, with both dates inclusive')
    else:
        run_day = ANCHOR + timedelta(days=((local.date() - ANCHOR).days // 14) * 14)
        first, last = run_day - timedelta(days=14), run_day - timedelta(days=1)
    return {'start_date': first.isoformat(), 'end_date': last.isoformat()}


def schedule_due(now):
    local = now.astimezone(ZoneInfo('America/Los_Angeles'))
    return local.date() >= ANCHOR and (local.date() - ANCHOR).days % 14 == 0 and local.hour >= 8


def fetch_tcp(dates):
    client = TCP()
    client.authenticate()
    first, last = dates['start_date'], dates['end_date']
    queries = {'locations': {}, 'jobcodes': {}, 'employees': {'includeRoles': 'true'},
               'employeejobcodes': {'includeSuspendedEmployees': 'true', 'includeTerminatedEmployees': 'true'},
               'hours': {'startDate': first, 'stopDate': (date.fromisoformat(last) + timedelta(days=1)).isoformat(),
                         'includeJobCode': 'true', 'includeRate': 'false', 'includeOverlapping': 'false'},
               'worksegments': {'startDate': first + 'T00:00:00', 'stopDate': last + 'T23:59:59',
                                'includeSegmentTime': 'timeIn', 'includeEmployeeDefaultLocation': 'true',
                                'sortByWorkSegmentId': 'true'}}
    report = {'generated_at': datetime.now(timezone.utc).isoformat(),
              'period': {'startDate': first, 'stopDate': last}, 'datasets': {}, 'errors': {}}
    for kind, params in queries.items():
        rows = client.collect(kind, params)
        report['datasets'][kind] = {'complete': True, 'count': len(rows), 'records': rows}
        print(f'TCP {kind}: {len(rows)} records retrieved')
    return report


def hour_text(minutes):
    value = Decimal(str(minutes)) / 60
    if not value.is_finite() or value < 0:
        raise ValueError('Negative or invalid TCP hours')
    return f'{value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP):.2f}'


def write_issues(report, payroll, output):
    """A compact private correction list instead of asking users to read raw JSON."""
    rows = []
    for segment in summarize(report)['segments']:
        if segment['category'] in ('Open punch', 'Zero-hour shift'):
            rows.append([segment['employee_id'], segment['name'], segment['store'], segment['segment_id'],
                         segment['time_in'], segment['time_out'] or '', segment['category']])
    people = {str(p['employeeId']): p for p in report['datasets']['employees']['records']}
    for issue in payroll['issues']:
        if issue.startswith('GEID '):
            eid, detail = issue[5:].split(': ', 1)
            person = people.get(eid, {})
            rows.append([eid, person.get('employeeName', ''), store_label(person.get('location')), '', '', '', detail])
        elif issue.startswith('No calculated worked shifts'):
            rows.append(['', '', '', '', '', '', issue])
    with (output / ('TCP corrections ' + date_label(payroll['period']) + '.csv')).open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['GEID', 'Name', 'Store', 'Segment ID', 'Clock in', 'Clock out', 'Issue'])
        for row in rows:
            writer.writerow(["'" + str(v) if str(v).startswith(('=', '+', '-', '@')) else v for v in row])


def normalize(report):
    summary = summarize(report)
    checks = summary['checks']
    issues = [f'TCP check requires correction: {key} ({len(checks[key]) if isinstance(checks[key], list) else checks[key]})'
              for key in ('hours_only_ids', 'work_only_ids', 'open_punches', 'zero_hour_closed_shifts') if checks[key]]
    first_date = date.fromisoformat(report['period']['startDate'])
    last_date = date.fromisoformat(report['period']['stopDate'])
    worked_dates = {s['time_in'][:10] for s in summary['segments'] if s['category'] == 'Worked'}
    missing_dates = [(first_date + timedelta(days=i)).isoformat() for i in range((last_date - first_date).days + 1)
                     if (first_date + timedelta(days=i)).isoformat() not in worked_dates]
    if missing_dates:
        issues.append('No calculated worked shifts on dates: ' + ', '.join(missing_dates) + '; historical/archive data or closure verification required')
    datasets = report['datasets']
    if not datasets.get('employeejobcodes', {}).get('complete'):
        raise ValueError('Complete TCP employee job assignments are required')
    if {store_label(r['name']) for r in datasets['locations']['records'] if r.get('active', True)} != set(STORES):
        issues.append('TCP active store list differs from the 18 configured stores')
    jobs = {str(r['jobCodeId']): r for r in datasets['jobcodes']['records']}
    assignments = defaultdict(list)
    first, last = report['period']['startDate'], report['period']['stopDate']
    for row in datasets['employeejobcodes']['records']:
        start, stop = str(row.get('activePeriodStartDate') or '')[:10], str(row.get('activePeriodStopDate') or '')[:10]
        if start and start > last or stop and stop < first:
            continue
        assignments[str(row['employeeId'])].append(row)
    catalog = {str(r['employeeId']): dict(r) for r in datasets['employees']['records']}
    by_person = defaultdict(list)
    for row in summary['employees']:
        by_person[row['employee_id']].append(row)
    for row in datasets['hours']['records']:
        person = row['employee']
        eid = str(person['employeeId'])
        if eid in by_person and eid not in catalog:
            catalog[eid] = person
    roster, employees = [], []
    for eid, person in sorted(catalog.items()):
        worked = by_person[eid]
        if not worked:
            if (person.get('terminationDate') or '')[:10] and person['terminationDate'][:10] < first:
                continue
            if (person.get('hireDate') or '')[:10] > last:
                continue
        try:
            if not person.get('firstName') or not person.get('lastName'):
                raise ValueError('first/last name unavailable')
            titles = set()
            defaults = [a for a in assignments[eid] if a.get('isDefault')]
            for a in defaults:
                job = jobs.get(str(a['jobCodeId']), {})
                titles |= roles((job.get('description') or a.get('description') or '').split(' - ')[0])
            # Most employees have no default flag. A single distinct active
            # operational title is unambiguous even across multiple stores.
            if not titles:
                assigned_titles = set()
                for a in assignments[eid]:
                    if not a.get('isActive'):
                        continue
                    description = (jobs.get(str(a['jobCodeId']), {}).get('description') or a.get('description') or '').split(' - ')[0]
                    try:
                        assigned_titles |= roles(description)
                    except ValueError:
                        continue  # Sick/bonus/tipping codes are not staff titles.
                if len(assigned_titles) == 1:
                    titles = assigned_titles
                elif len(assigned_titles) > 1:
                    raise ValueError('conflicting active job titles without a default')
            for row in worked:
                titles |= roles(row['roles'])
            if not titles:
                raise ValueError('no verified default or worked job title')
            if not worked:
                home = store_label(person.get('location'))
                if home not in STORES:
                    assigned_stores = {store_label(a.get('location') or jobs.get(str(a['jobCodeId']), {}).get('locationName'))
                                       for a in assignments[eid] if a.get('isActive')}
                    assigned_stores.discard('Unmapped')
                    if len(assigned_stores) == 1:
                        home = assigned_stores.pop()
                worked = [{'store': home, 'regular_minutes': 0, 'ot1_minutes': 0, 'ot2_minutes': 0}]
            if any(row['store'] not in STORES for row in worked):
                raise ValueError('store could not be mapped')
            roster.append({'geid': eid, 'first_name': person['firstName'], 'last_name': person['lastName'],
                           'roles': sorted(titles), 'payroll_ids': []})
            for row in worked:
                regular = Decimal(str(row['regular_minutes']))
                overtime = Decimal(str(row['ot1_minutes'])) + Decimal(str(row['ot2_minutes']))
                total, ot = hour_text(regular + overtime), hour_text(overtime)
                # Preserve rounded total and OT; regular is their displayed remainder.
                # Exact source minutes remain in the private audit JSON.
                reg = f'{Decimal(total) - Decimal(ot):.2f}'
                employees.append({'store': row['store'], 'employee_id': eid,
                                  'name': person['firstName'] + ' ' + person['lastName'], 'roles': sorted(titles),
                                  'total_hours': total, 'regular_hours': reg, 'overtime_hours': ot,
                                  'source_regular_minutes': str(regular), 'source_ot_minutes': str(overtime)})
        except ValueError as exc:
            issues.append(f'GEID {eid}: {exc}')
    if not roster:
        issues.append('No complete roster was returned')
    return {'period': {'start_date': first, 'end_date': last}, 'verified': not issues, 'issues': issues,
            'checks': checks, 'roster': roster, 'employees': employees}


def style(sheet, widths):
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    for cell in sheet[1]:
        cell.fill = PatternFill('solid', fgColor='163C53')
        cell.font = Font(color='FFFFFF', bold=True)
        cell.alignment = Alignment(wrap_text=True)
    sheet.row_dimensions[1].height = 30
    for i, width in enumerate(widths, 1):
        from openpyxl.utils import get_column_letter
        sheet.column_dimensions[get_column_letter(i)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, (float, int, Decimal)):
                cell.number_format = '0.00'
            elif isinstance(cell.value, str):
                cell.data_type = 's'  # Names/IDs cannot become formulas.
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = '1:1'


def prepare(gateway_data, payroll, output):
    allocation = allocate(gateway_data, payroll)
    if {r['store'] for r in allocation['stores']} != set(STORES):
        raise ValueError('Gateway must cover all 18 stores')
    output.mkdir(parents=True, exist_ok=True)
    people = {p['geid']: p for p in payroll['roster']}
    hours = {(p['store'], p['employee_id']): p for p in payroll['employees']}
    manifest = {'period': payroll['period'], 'verified': True, 'roster_verified': True,
                'cross_store_ot_verified': True, 'issues': [], 'expected_stores': STORES,
                'gateway_tips': {r['store']: r['gateway_tips'] for r in allocation['stores']},
                'roster': payroll['roster'], 'workbooks': [],
                'ot_basis': 'Sum TCP calculated overtime1 and overtime2, preserving classification across stores'}
    label = date_label(payroll['period'])
    for store in allocation['stores']:
        sid = store['store']
        book = Workbook()
        sheet = book.active
        sheet.title = 'Payroll'
        sheet.append(FIELDS)
        rows = sorted((r for r in allocation['employees'] if r['store'] == sid),
                      key=lambda r: (not r['eligible'], people[r['employee_id']]['last_name'].casefold(), r['employee_id']))
        for row in rows:
            person, h = people[row['employee_id']], hours[(sid, row['employee_id'])]
            sheet.append([row['employee_id'], '', person['first_name'], person['last_name'],
                          '; '.join(sorted({LABELS[r] for r in row['roles']})), Decimal(row['total_hours']),
                          Decimal(row['tips']), Decimal(h['regular_hours']), Decimal(h['overtime_hours'])])
        style(sheet, [14, 13, 18, 20, 28, 14, 14, 14, 14])
        # Totals in a separate sheet cannot accidentally be counted as employees.
        totals = book.create_sheet('Store totals')
        for values in [('Metric', 'Value'), ('Store', sid), ('Pay period', label),
                       ('Gateway tips', Decimal(store['gateway_tips'])),
                       ('Eligible crew hours', Decimal(store['eligible_crew_hours'])),
                       ('Tips per eligible hour', Decimal(store['gateway_tips']) / Decimal(store['eligible_crew_hours']) if Decimal(store['eligible_crew_hours']) else 0),
                       ('Allocated tips', Decimal(store['allocated_tips'])),
                       ('Tip rule', 'Gateway tips / eligible crew decimal hours * individual decimal hours'),
                       ('Eligible titles', 'Crew, Crew Member, Crew Leader'),
                       ('Excluded titles', 'Manager, Co-manager, Assistant Manager'),
                       ('Hours rounding', 'Total and OT rounded to .01; regular = total - OT. Exact TCP minutes in audit.'),
                       ('Penny rounding', allocation['rounding'])]:
            totals.append(values)
        style(totals, [28, 100])
        totals['B6'].number_format = '0.000000'
        filename = f'{sid} {label}.xlsx'
        book.save(output / filename)
        manifest['workbooks'].append({'store': sid, 'file': filename, 'sheet': 'Payroll', 'header_row': 1})
    source_rows, sources = read_store_workbooks(manifest, output)
    summary = summarize_rows(manifest, source_rows)
    combined = Workbook()
    combined.remove(combined.active)
    for title, rows in (('All employees', summary['employees']), ('Managers', summary['managers'])):
        sheet = combined.create_sheet(title)
        sheet.append([c[0] for c in COLUMNS])
        for row in rows:
            sheet.append([Decimal(row[key]) if key in ('regular_hours', 'overtime_hours', 'total_hours', 'tips') else display(row, key) for _, key in COLUMNS])
        style(sheet, [14, 13, 18, 20, 28, 35, 16, 16, 16, 14])
    file = output / f'Combined payroll {label}.xlsx'
    combined.save(file)
    sources.append({'path': str(file), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
    message = build_message(summary, sources)
    message['Message-ID'] = f'<payroll-{payroll["period"]["start_date"]}-{payroll["period"]["end_date"]}@caazllc.com>'
    if len(message.as_bytes()) > 24_000_000:
        raise ValueError('Payroll email exceeds the 24 MB encoded message limit')
    for name, data in (('manifest', manifest), ('allocation', allocation), ('combined-payroll', summary)):
        (output / f'{name}.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
    (output / 'payroll-email.eml').write_bytes(message.as_bytes())
    (output / 'payroll-email.html').write_text(message.get_body(preferencelist=('html',)).get_content(), encoding='utf-8')
    return message, summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--mode', choices=['preflight', 'preview', 'deliver'], default='preflight')
    parser.add_argument('--schedule-gate', action='store_true')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.schedule_gate:
        print('true' if schedule_due(now) else 'false')
        return 0
    dates = period_for(now, args.start, args.end)
    output = FOLDER / 'reports' / (dates['start_date'] + '_' + dates['end_date'])
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(FOLDER.parent / 'sales automation'))
    from daily_report import DeliveryLedger, deliver
    ledger = None
    if args.mode == 'deliver':
        ledger = DeliveryLedger(dates['end_date'], namespace='payroll')
        previous = ledger.read()
        if previous:
            if previous.get('status') == 'sent':
                print('Payroll period already delivered; no changes made')
                return 0
            raise ValueError('Previous payroll email outcome unresolved; check Gmail Sent before retrying')
    missing = gateway.missing_secrets() + [key for key in ('TCP_CLIENT_ID', 'TCP_CLIENT_SECRET', 'TCP_API_KEY', 'CAAZ_GMAIL_APP_PASS') if not os.environ.get(key)]
    if missing and args.mode != 'preflight':
        raise ValueError('Missing secrets: ' + ', '.join(missing))
    payroll = None
    if all(os.environ.get(key) for key in ('TCP_CLIENT_ID', 'TCP_CLIENT_SECRET', 'TCP_API_KEY')):
        report = fetch_tcp(dates)
        (output / 'tcp-audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        payroll = normalize(report)
        (output / 'payroll-input.json').write_text(json.dumps(payroll, indent=2), encoding='utf-8')
        write_issues(report, payroll, output)
    tips = None
    if not gateway.missing_secrets():
        tips = gateway.fetch(dates)
        (output / 'gateway-input.json').write_text(json.dumps(tips, indent=2), encoding='utf-8')
    issues = ['Missing secret: ' + key for key in missing] + (payroll['issues'] if payroll else ['TCP was not checked'])
    status = {'period': dates, 'ready': not issues, 'issues': issues,
              'tcp_roster_count': len(payroll['roster']) if payroll else 0,
              'gateway_store_count': len(tips['stores']) if tips else 0}
    (output / 'readiness.json').write_text(json.dumps(status, indent=2), encoding='utf-8')
    print(json.dumps({**status, 'issues': [i for i in issues if not i.startswith('GEID')] + [f'{sum(i.startswith("GEID") for i in issues)} employee mapping issues (private artifact)']}, indent=2))
    if issues:
        return 1
    if args.mode == 'preflight':
        return 0
    message, summary = prepare(tips, payroll, output)
    print(f'Prepared 18 store workbooks and combined workbook for {len(summary["employees"])} people')
    if ledger:
        print('Payroll email ' + deliver(message, ledger, dates['end_date']))
    else:
        print('Private preview only; no email sent')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Arbitrary transport errors can contain URLs or credentials. Private
        # input/readiness artifacts hold business-data issues when available.
        print(f'Payroll stopped ({type(exc).__name__}); no success is assumed', file=sys.stderr)
        raise SystemExit(1)
