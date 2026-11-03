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
from regional_email import build_region_messages, regional_filename, delivery_ledgers, send_regions
from payroll_email_summary import (roles, LABELS, FIELDS, date_label, read_store_workbooks,
                                   summarize_rows, build_message, COLUMNS, display, regional_people)
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
                         'includeJobCode': 'true', 'includeRate': 'true', 'includeOverlapping': 'false'},
               'worksegments': {'startDate': first + 'T00:00:00', 'stopDate': last + 'T23:59:59',
                                'includeSegmentTime': 'timeIn', 'includeEmployeeDefaultLocation': 'true',
                                'sortByWorkSegmentId': 'true'}}
    report = {'generated_at': datetime.now(timezone.utc).isoformat(),
              'period': {'startDate': first, 'stopDate': last}, 'datasets': {}, 'errors': {}}
    for kind, params in queries.items():
        rows = client.collect(kind, params, keep_rate=kind == 'hours')
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


def tcp_rates(report):
    """GEID -> hourly rate text from TCP: the rate on the person's latest worked shift in the period.

    The owner pays the TCP rate; these replace hourly rates in the ADP import rows.
    A shift without a positive rate is ignored; people without one keep ADP's rate.
    """
    first, last = report['period']['startDate'], report['period']['stopDate']
    latest = {}
    for item in report['datasets']['hours']['records']:
        eid = str(item['employee']['employeeId'])
        for segment in item.get('segments', []):
            start = str(segment.get('timeIn') or '')
            try:
                rate = Decimal(str(segment.get('rate')))
            except Exception:
                continue
            if not (first <= start[:10] <= last) or not rate.is_finite() or rate <= 0:
                continue
            if eid not in latest or start > latest[eid][0]:
                latest[eid] = (start, rate)
    return {eid: f'{rate:.4f}' for eid, (_, rate) in latest.items()}


ADP_TITLE_ROLES = {'team member': 'crew member', 'crew': 'crew member', 'crew member': 'crew member',
                   'team leader': 'crew leader', 'crew leader': 'crew leader',
                   'assistant manager': 'assistant manager', 'co-manager': 'co-manager',
                   'general manager': 'manager', 'manager': 'manager', 'manager trainee': 'manager in training'}


def adp_roles_by_geid(catalog):
    """TCP GEID -> payroll role from ADP job titles, matched by exact unique name.

    Only active ADP assignments count. A name shared by different ADP people, an
    unmapped title, or one person with conflicting titles gives no ADP role, so
    the TCP problem stays on the issues list. Returns {} when ADP is unavailable.
    """
    try:
        import adp_api
        if adp_api.missing_secrets():
            return {}
        workers, _ = adp_api.workers_once()
    except Exception:
        return {}
    by_name = defaultdict(lambda: (set(), set()))
    for worker in workers or []:
        if not adp_api.is_active(worker):
            continue
        key = (adp_api.name_key(worker['first_name']), adp_api.name_key(worker['last_name']))
        people, found = by_name[key]
        people.add(worker['associate_oid'])
        found.add(ADP_TITLE_ROLES.get(adp_api.name_key(worker['job_title'])))
    result = {}
    for eid, person in catalog.items():
        key = (adp_api.name_key(person.get('firstName') or ''), adp_api.name_key(person.get('lastName') or ''))
        people, found = by_name.get(key, (set(), set()))
        if len(people) == 1 and len(found) == 1 and None not in found:
            result[eid] = next(iter(found))
    return result


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
    # Each person's active TCP staff titles; standing owner confirmations must still match them.
    tcp_titles = {eid: sorted({(jobs.get(str(a['jobCodeId']), {}).get('description') or '').split(' - ')[0].strip()
                               for a in rows if a.get('isActive')
                               and not jobs.get(str(a['jobCodeId']), {}).get('leaveCode')} - {''})
                  for eid, rows in assignments.items()}
    from role_reviews import confirmed_roles_for_period
    confirmed_roles = confirmed_roles_for_period({'start_date': first, 'end_date': last}, tcp_titles)
    catalog = {str(r['employeeId']): dict(r) for r in datasets['employees']['records']}
    by_person = defaultdict(list)
    for row in summary['employees']:
        by_person[row['employee_id']].append(row)
    for row in datasets['hours']['records']:
        person = row['employee']
        eid = str(person['employeeId'])
        if eid in by_person and eid not in catalog:
            catalog[eid] = person
    # ADP's job title settles a TCP title problem (conflicting titles, an
    # unrecognized title such as Training, or an open role review); an owner
    # confirmation still wins. ADP is read once per run and never written.
    adp_roles = adp_roles_by_geid(catalog)
    owner_roles = confirmed_roles
    confirmed_roles = {**adp_roles, **owner_roles}
    adp_used = set()
    roster, employees, warnings = [], [], []
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
                try:
                    titles |= roles((job.get('description') or a.get('description') or '').split(' - ')[0])
                except ValueError:
                    if eid not in confirmed_roles:
                        raise
                    titles.add(confirmed_roles[eid])  # Unrecognized default title such as Training.
                    if eid not in owner_roles:
                        adp_used.add(eid)
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
                elif assigned_titles and assigned_titles <= MANAGEMENT:
                    # Multiple management titles share the same non-tip status.
                    # Preserve every title; do not invent a preferred/default job.
                    titles = assigned_titles
                elif len(assigned_titles) > 1 and eid in confirmed_roles and (
                        eid in owner_roles or confirmed_roles[eid] in assigned_titles):
                    # An owner confirmation, or ADP's title when it is one of the
                    # TCP titles, resolves eligibility. Retain all TCP titles until
                    # the final role-review pass saves them and applies that role.
                    titles = assigned_titles
                    if eid not in owner_roles:
                        adp_used.add(eid)
                elif len(assigned_titles) > 1:
                    raise ValueError('conflicting active job titles without a default')
            for row in worked:
                try:
                    titles |= roles(row['roles'])
                except ValueError:
                    if eid not in confirmed_roles:
                        raise
                    titles.add(confirmed_roles[eid])  # Owner-confirmed or ADP title for an unrecognized TCP title.
                    if eid not in owner_roles:
                        adp_used.add(eid)
            if not titles:
                if worked:
                    raise ValueError('no verified default or worked job title')
                titles = {'unassigned'}
                warnings.append(f'GEID {eid}: no worked shifts; job title unassigned, retained with zero hours and tips')
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
                if titles == {'unassigned'} and not by_person[eid]:
                    warnings.append(f'GEID {eid}: no home store; retained in the combined roster only')
                    worked = []
                else:
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
    from role_reviews import apply_role_reviews
    return apply_role_reviews({'period': {'start_date': first, 'end_date': last}, 'verified': not issues, 'issues': issues,
            'checks': checks, 'roster': roster, 'employees': employees, 'warnings': warnings,
            'tcp_titles': tcp_titles, 'tcp_rates': tcp_rates(report),
            'adp_roles': {eid: adp_roles[eid] for eid in adp_used},
            'adp_titles': adp_roles})


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


def prepare_legacy(gateway_data, payroll, output):
    allocation = allocate(gateway_data, payroll)
    if {r['store'] for r in allocation['stores']} != set(STORES):
        raise ValueError('Gateway must cover all 18 stores')
    output.mkdir(parents=True, exist_ok=True)
    people = {p['geid']: p for p in payroll['roster']}
    hours = {(p['store'], p['employee_id']): p for p in payroll['employees']}
    manifest = {'period': payroll['period'], 'verified': True, 'roster_verified': True,
                'cross_store_ot_verified': True, 'issues': [], 'expected_stores': STORES,
                'gateway_tips': {r['store']: r['gateway_tips'] for r in allocation['stores']},
                'roster': payroll['roster'], 'workbooks': [], 'warnings': payroll.get('warnings', []),
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
    regional_columns = [('GEID', 'geid'), ('Payroll ID', 'payroll_ids'), ('First Name', 'first_name'),
                        ('Last Name', 'last_name'), ('Job Title', 'job_title'), ('Total Hours', 'total_hours'),
                        ('Tips', 'tips'), ('Reg Hours', 'regular_hours'), ('OT Hours', 'overtime_hours')]
    for region, rows in regional_people(summary).items():
        regional_book = Workbook()
        sheet = regional_book.active
        sheet.title = region
        sheet.append([c[0] for c in regional_columns])
        for row in rows:
            sheet.append([Decimal(row[key]) if key in ('regular_hours', 'overtime_hours', 'total_hours', 'tips')
                          else display(row, key) for _, key in regional_columns])
        last = sheet.max_row
        sheet.append(['TOTAL', '', '', '', '', *[f'=SUM({col}2:{col}{last})' if last > 1 else 0 for col in ('F', 'G', 'H', 'I')]])
        style(sheet, [14, 13, 18, 20, 28, 14, 14, 14, 14])
        # style() treats text as literal for injection protection; totals are
        # trusted formulas and must be written after that pass.
        for col in ('F', 'G', 'H', 'I'):
            sheet[f'{col}{last + 1}'] = f'=SUM({col}2:{col}{last})' if last > 1 else 0
        file = output / regional_filename(region, payroll['period'])
        regional_book.save(file)
        sources.append({'path': str(file), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
    messages = build_region_messages(summary, sources)
    for name, data in (('manifest', manifest), ('allocation', allocation), ('combined-payroll', summary)):
        (output / f'{name}.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
    for message in messages:
        (output / (message['Subject'] + '.eml')).write_bytes(message.as_bytes())
    return messages, summary


def prepare(gateway_data, payroll, output):
    from report_package import prepare_current, verify_stores
    from adp_roster import FILES
    import base64
    templates = output / 'adp-templates'
    templates.mkdir(parents=True, exist_ok=True)
    settings_text = os.environ.get('ADP_ROSTER_SETTINGS', '')
    if not settings_text:
        raise ValueError('Current-period ADP roster settings and exports are required')
    settings = json.loads(settings_text)
    from review_adp import current_template, identity_overrides
    roster_notes = {}
    for code, company, filename in FILES.values():
        raw, live_notes, added, live = current_template(code, company, payroll['period']['start_date'])
        if raw is None:
            raise ValueError('Missing current ADP template for ' + code)
        if not live and settings.get('period') != payroll['period']:
            raise ValueError('Refresh ADP exports for the new payroll period before creating import files')
        (templates / filename).write_bytes(raw)
        overrides, identity_notes = identity_overrides(raw, company, code, settings)
        settings.setdefault('identities', {})[code] = overrides
        roster_notes[code] = optional_roster_notes(live_notes, identity_notes, added)
    # Recorded by build_imports as warnings: visible, but never a readiness change.
    settings['roster_notes'] = roster_notes
    settings['tcp_rates'] = payroll.get('tcp_rates', {})  # The owner pays TCP's hourly rate.
    log_roster_notes(roster_notes)
    messages, data, audit = prepare_current(gateway_data, payroll, output, templates, settings)
    verify_stores(data, output)
    if not audit.get('reports_deliverable'):
        raise ValueError('Payroll cannot be fully accounted for; inspect private adp-readiness.json')
    return messages, data['summary']


def optional_roster_notes(live_notes, identity_notes, added):
    """Live-roster and confirmed-identity notes for one region, in adp-readiness.json form; never raises."""
    entry = {'roster_notes': [], 'identity_notes': [], 'adp_added': []}
    try:
        entry['roster_notes'] = [str(n) for n in live_notes or []]
        entry['identity_notes'] = [str(n) for n in identity_notes or []]
        entry['adp_added'] = [{k: str(row.get(k, '')) for k in ('file_number', 'name', 'rate', 'hire_date')}
                              for row in added or [] if isinstance(row, dict)]
    except Exception:
        pass
    return entry


def log_roster_notes(roster_notes):
    """Counts only in the Actions log: identity notes carry GEIDs and ADP file numbers."""
    try:
        from adp_identities import INVALID
        invalid = False
        for code, entry in roster_notes.items():
            live, identity = entry.get('roster_notes', []), entry.get('identity_notes', [])
            invalid = invalid or INVALID in identity
            if live or identity:
                print(f'{code}: {len(live)} live ADP roster note(s) and {len(identity)} confirmed-identity note(s); '
                      'see private adp-readiness.json and ADP import checks.txt')
        if invalid:
            print(INVALID)
    except Exception:
        pass


def step_summary(line):
    """Append one line to the GitHub Actions job summary when there is one; never raises."""
    try:
        path = os.environ.get('GITHUB_STEP_SUMMARY')
        if path:
            with open(path, 'a', encoding='utf-8') as file:
                file.write(line + '\n')
    except Exception:
        pass


def zero_hours_allowed():
    return (os.environ.get('GITHUB_EVENT_NAME') == 'schedule'
            or (os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch' and os.environ.get('ZERO_HOURS_MANUAL_SEND') == 'true'))


def zero_hours_step(mode, dates, report, output, settings=None):
    """Build, and in deliver mode send once, the 0-hours termination-review email.

    Independent of payroll readiness; it needs complete TCP punches and the live
    ADP roster. Confirmed matches are the same ones payroll uses: the
    PAYROLL_ADP_IDENTITIES secret plus the saved ADP_ROSTER_SETTINGS identities
    (settings). Any problem is recorded and never stops payroll; this never raises.
    """
    folder = output / 'zero-hours'
    result = {'period': dates, 'status': 'skipped'}
    notes = []
    try:
        folder.mkdir(parents=True, exist_ok=True)
        sales = str(FOLDER.parent / 'sales automation')
        if sales not in sys.path:
            sys.path.insert(0, sales)
        import adp_api
        from adp_identities import merged
        from zero_hours_notice import build_message, tcp_sources_complete
        if report is None or not tcp_sources_complete(report):
            result['reason'] = 'TCP punches are unavailable or incomplete'
        elif adp_api.missing_secrets():
            result['reason'] = 'ADP API secrets are missing'
        else:
            workers, problem = adp_api.workers_once()
            if workers is None:
                result['reason'] = 'ADP roster unavailable: ' + str(problem)
            else:
                identities, problems = merged(settings)
                notes.extend(problems)
                message, data = build_message(dates, report, workers, identities, notes=notes)
                (folder / 'zero-hours.eml').write_bytes(message.as_bytes())
                (folder / 'zero-hours.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
                result.update(counts={code: {k: len(v) for k, v in lists.items() if isinstance(v, (list, tuple))}
                                      for code, lists in data.items() if isinstance(lists, dict)},
                              to=message['To'], cc=message['Cc'], subject=message['Subject'], status='preview')
                # Scheduled payroll runs, or a manual run the owner explicitly asked to send it;
                # ordinary manual resends or corrections never send this email.
                if (mode == 'deliver' and os.environ.get('CAAZ_GMAIL_APP_PASS') and zero_hours_allowed()):
                    from daily_report import DeliveryLedger, deliver
                    ledger = DeliveryLedger(dates['end_date'] + '-zero-hours', namespace='payroll')
                    result['status'] = deliver(message, ledger, dates['end_date'])
    except Exception as exc:
        # These messages are fixed module texts (at most an HTTP status), never credentials.
        safe = isinstance(exc, ValueError) or type(exc).__name__ in ('ReportError', 'ADPError')
        result.update(status='failed', reason=type(exc).__name__ + (': ' + str(exc) if safe else ''))
    try:
        result['notes'] = [str(n) for n in notes]
        (folder / 'zero-hours-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    except Exception as exc:
        print('Zero-hours result could not be saved (' + type(exc).__name__ + ')')
    shown = result['status'] + (' (' + str(result['reason']) + ')' if result.get('reason') else '')
    print('Zero-hours email: ' + shown)
    if result.get('notes'):
        # Notes can name employees: only the count goes to the Actions log.
        print(f"Zero-hours notes: {len(result['notes'])}; see private zero-hours/zero-hours-result.json")
    if (mode == 'deliver' and zero_hours_allowed()
            and result['status'] not in ('sent', 'already sent')):
        step_summary('0-hours termination email NOT sent: ' + shown + '. Later scheduled slots on this payroll day '
                     'retry it only while no zero-hours receipt exists; manual runs never send it.')
    return result


def zero_hours_catch_up(dates, output):
    """Retry the 0-hours email in a scheduled slot after final payroll was already delivered.

    Runs only for scheduled runs and only while no '<end>-zero-hours' receipt
    exists in any status: 'sent' is done, and 'sending' is an uncertain earlier
    send that needs a Gmail Sent check, so neither is retried. Returns the step
    result, or None when nothing was attempted. Never raises.
    """
    try:
        if os.environ.get('GITHUB_EVENT_NAME') != 'schedule':
            return None
        sales = str(FOLDER.parent / 'sales automation')
        if sales not in sys.path:
            sys.path.insert(0, sales)
        from daily_report import DeliveryLedger
        receipt = DeliveryLedger(dates['end_date'] + '-zero-hours', namespace='payroll').read()
        if receipt is not None:
            status = receipt.get('status') if isinstance(receipt, dict) else None
            print('Zero-hours email: receipt already exists (' + str(status) + '); no resend.')
            if status != 'sent':
                step_summary('0-hours termination email receipt is ' + repr(status) + ', not sent: check Gmail Sent '
                             'before manually clearing the zero-hours receipt; it is never resent automatically.')
            return None
        report = None
        if all(os.environ.get(key) for key in ('TCP_CLIENT_ID', 'TCP_CLIENT_SECRET', 'TCP_API_KEY')):
            try:
                report = fetch_tcp(dates)
            except Exception as exc:
                # zero_hours_step records 'skipped' for missing TCP punches.
                print('Zero-hours catch-up: TCP read failed (' + type(exc).__name__ + ')')
        settings, _ = roster_setup(dates)
    except Exception as exc:
        print('Zero-hours catch-up skipped (' + type(exc).__name__ + ')')
        step_summary('0-hours termination email NOT sent: catch-up could not check its receipt ('
                     + type(exc).__name__ + ').')
        return None
    try:
        return zero_hours_step('deliver', dates, report, output, settings)
    except Exception as exc:
        print('Zero-hours catch-up failed (' + type(exc).__name__ + ')')
        return None


def roster_setup(dates):
    """Reject old period controls without blocking non-import review reports."""
    try:
        settings = json.loads(os.environ.get('ADP_ROSTER_SETTINGS') or '{}')
    except (ValueError, TypeError):
        return {}, ['ADP roster settings are invalid; obtain current-period ADP exports.']
    if not isinstance(settings, dict):
        return {}, ['ADP roster settings are invalid; obtain current-period ADP exports.']
    issues = []
    if live_roster_available():
        return settings, issues  # The EPI files are built from the live ADP roster this run.
    if settings.get('period') != dates:
        issues.append('Current-period ADP roster is not verified; EPI candidate retains the last supplied roster and batch controls for review.')
    for region in ('CO', 'AZ', 'TX'):
        if not os.environ.get('ADP_' + region + '_TEMPLATE'):
            issues.append('Missing current ADP export: ' + region)
    return settings, issues


def live_roster_available():
    try:
        import adp_api
        return not adp_api.missing_secrets() and bool(adp_api.workers_once()[0])
    except Exception:
        return False


def main():
    from review_package import review_revision
    parser = argparse.ArgumentParser()
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--mode', choices=['preflight', 'preview', 'deliver'], default='preflight')
    parser.add_argument('--review-revision', type=review_revision, default='',
                        help='Explicit correction slug for review emails; blank retains the original review receipt')
    parser.add_argument('--schedule-gate', action='store_true')
    parser.add_argument('--tcp-export', type=Path, action='append', default=[],
                        help='Native Complete Payroll XML to reconcile zero API hours; repeat for separate dates')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.schedule_gate:
        print('true' if schedule_due(now) else 'false')
        return 0
    dates = period_for(now, args.start, args.end)
    output = FOLDER / 'reports' / (dates['start_date'] + '_' + dates['end_date'])
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(FOLDER.parent / 'sales automation'))
    ledgers = None
    if args.mode == 'deliver':
        ledgers = delivery_ledgers(dates['end_date'])
        if all((ledger.read() or {}).get('status') == 'sent' for ledger in ledgers.values()):
            print('Regional emails were already sent; delivery alone does not establish payroll completeness. No resend.')
            # A 0-hours email that an earlier scheduled slot could not send gets
            # one more try under its own receipt; payroll itself is never resent.
            zero_hours_catch_up(dates, output)
            return 2
    required_tcp = ('TCP_CLIENT_ID', 'TCP_CLIENT_SECRET', 'TCP_API_KEY')
    missing = gateway.missing_secrets() + [key for key in required_tcp if not os.environ.get(key)]
    if args.mode == 'deliver' and not os.environ.get('CAAZ_GMAIL_APP_PASS'):
        missing.append('CAAZ_GMAIL_APP_PASS')
    payroll, report = None, None
    collection_issues = []
    collection_errors = []
    if all(os.environ.get(key) for key in ('TCP_CLIENT_ID', 'TCP_CLIENT_SECRET', 'TCP_API_KEY')):
        from tcp_inspect import InspectionError
        try:
            report = fetch_tcp(dates)
            (output / 'tcp-audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            if args.tcp_export:
                from tcp_report_fallback import apply_export
                for native_xml in args.tcp_export:
                    report = apply_export(report, native_xml)
                (output / 'tcp-reconciled-audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            payroll = normalize(report)
            (output / 'payroll-input.json').write_text(json.dumps(payroll, indent=2), encoding='utf-8')
            write_issues(report, payroll, output)
        except InspectionError as exc:
            collection_issues.append('TCP collection failed; no complete calculated-hour source is available. Inspect API access and rerun.')
            # InspectionError messages carry only status codes and TCP's error label, never credentials.
            collection_errors.append({'source': 'TCP', 'detail': str(exc)})
            print('TCP: ' + str(exc), file=sys.stderr)
        except (ValueError, KeyError, TypeError):
            collection_issues.append('TCP source validation failed. Review the private TCP audit/native export; hours must not be inferred.')
    tips = None
    if not gateway.missing_secrets():
        try:
            tips = gateway.fetch(dates)
            (output / 'gateway-input.json').write_text(json.dumps(tips, indent=2), encoding='utf-8')
        except gateway.GatewayError as exc:
            collection_issues.append('Gateway tips collection failed; no complete store tip pools are available. Check Gateway access and rerun.')
            # GatewayError already reduces browser exceptions to safe labels;
            # retain its region/stage instead of hiding every failure as generic.
            collection_errors.append({'source': 'Gateway', 'detail': str(exc)})
            print(str(exc), file=sys.stderr)
    (output / 'collection-errors.json').write_text(json.dumps(collection_errors, indent=2), encoding='utf-8')
    settings, roster_issues = roster_setup(dates)
    issues = (['Missing secret: ' + key for key in missing] + collection_issues + roster_issues
              + (payroll['issues'] if payroll else ['TCP hours are unavailable']))
    from source_reconciliation import reconcile
    comparison = reconcile(payroll, STORES, settings.get('source_reconciliation')) if payroll else None
    source_ready = comparison is not None and comparison['ready']
    status = {'period': dates, 'ready': not issues and source_ready, 'issues': issues,
              'source_reconciliation': comparison,
              'tcp_roster_count': len(payroll['roster']) if payroll else 0,
              'gateway_store_count': len(tips['stores']) if tips else 0}
    (output / 'readiness.json').write_text(json.dumps(status, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in status.items() if k not in ('issues', 'source_reconciliation')}))
    print(f'Source reconciliation ready: {source_ready}. See private readiness.json for details.')
    zero_hours_step(args.mode, dates, report, output, settings)
    if args.mode == 'preflight':
        return 0 if status['ready'] else 2
    if issues:
        from review_package import prepare_review, send_review
        messages = prepare_review(dates, output / 'REVIEW ONLY', issues, payroll, report, tips, comparison,
                                  revision=args.review_revision)
        print('Prepared three regional payroll packages with EPI candidates from available valid templates and issues sheets; payroll remains incomplete.')
        if args.mode == 'deliver' and os.environ.get('CAAZ_GMAIL_APP_PASS'):
            results = send_review(messages, dates['end_date'], revision=args.review_revision)
            (output / 'review-delivery-results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
            print('Regional review email receipts saved; this is not completed payroll.')
        return 2
    if args.review_revision:
        raise ValueError('Review revisions apply only to incomplete review packages; they cannot resend final payroll')
    messages, summary = prepare(tips, payroll, output)
    print(f'Prepared 18 store workbooks and three native ADP regional imports for {len(summary["employees"])} TCP people')
    if ledgers:
        send_regions(messages, dates['end_date'], ledgers)
    else:
        print('Private preview only; no email sent')
    from source_reconciliation import completion_code
    return completion_code(json.loads((output / 'adp-readiness.json').read_text(encoding='utf-8')))


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except gateway.GatewayError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        # Arbitrary transport errors can contain URLs or credentials. Private
        # input/readiness artifacts hold business-data issues when available.
        print(f'Payroll stopped ({type(exc).__name__}); no success is assumed', file=sys.stderr)
        raise SystemExit(1)
