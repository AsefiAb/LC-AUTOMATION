"""Deliver reports and incomplete EPI candidates when payroll checks need review.

These workbooks are review evidence, never a substitute for final payroll. Unknown
hours and unallocated tips stay blank. Review delivery has separate receipts so a
later corrected payroll package is not suppressed.
"""
import json
import os
import re
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path

from allocate_tips import MANAGEMENT
from payroll_email_summary import date_label
from regional_email import REGIONS
from summarize_inspection import store_label, summarize


FIELDS = ['GEID', 'Employee', 'Role', 'TCP regular hours returned',
          'TCP overtime hours returned', 'TCP total hours returned', 'Provisional tips', 'Review status']


def review_revision(value):
    """Explicit correction keys only; scheduled/default runs keep old receipts."""
    if not isinstance(value, str) or (value and (len(value) > 40 or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', value))):
        raise ValueError('Review revision must be blank or a lowercase letters/digits/hyphens slug, at most 40 characters')
    return value


def review_rows(dates, payroll=None, report=None, tips=None, comparison=None, allocation_audit=None):
    from payroll_run import STORES, hour_text
    rows, people = {}, {}
    if payroll and payroll['period'] != dates:
        raise ValueError('Review payroll dates differ')
    if report and report['period'] != {'startDate': dates['start_date'], 'stopDate': dates['end_date']}:
        raise ValueError('Review source dates differ')
    if tips and tips['period'] != dates:
        raise ValueError('Review Gateway dates differ')
    if payroll:
        people = {p['geid']: p for p in payroll['roster']}
        for p in payroll['employees']:
            person = people[p['employee_id']]
            rows[p['store'], p['employee_id']] = dict(
                store=p['store'], geid=p['employee_id'], first_name=person['first_name'],
                last_name=person['last_name'], roles=p['roles'],
                regular_hours=p['regular_hours'], overtime_hours=p['overtime_hours'],
                total_hours=p['total_hours'], tips=None)
    if report:
        data = report['datasets']
        catalog = {str(p['employeeId']): p for p in data.get('employees', {}).get('records', [])}
        jobs = {str(j['jobCodeId']): j for j in data.get('jobcodes', {}).get('records', [])}
        for item in data.get('hours', {}).get('records', []):
            person = item['employee']
            if any(dates['start_date'] <= (s.get('timeIn') or '')[:10] <= dates['end_date']
                   for s in item.get('segments', [])):
                catalog.setdefault(str(person['employeeId']), person)
        def add(store, eid):
            if (store, eid) not in rows:
                p = catalog.get(eid, {})
                rows[store, eid] = dict(store=store, geid=eid,
                    first_name=p.get('firstName', ''), last_name=p.get('lastName') or ('Unknown employee ' + eid),
                    roles=people.get(eid, {}).get('roles', ['unassigned']),
                    regular_hours=None, overtime_hours=None, total_hours=None, tips=None)
            return rows[store, eid]
        # Supplement normalization: unresolved roles and API-only people must
        # remain visible, even when they cannot be included in a payroll import.
        try:
            source = summarize(report)
        except (ValueError, KeyError, TypeError):
            source = {'employees': []}
        for item in source['employees']:
            row = add(item['store'], item['employee_id'])
            if row['regular_hours'] is None:
                ot = Decimal(str(item['ot1_minutes'])) + Decimal(str(item['ot2_minutes']))
                total = hour_text(Decimal(str(item['regular_minutes'])) + ot)
                row.update(regular_hours=f'{Decimal(total) - Decimal(hour_text(ot)):.2f}',
                           overtime_hours=hour_text(ot), total_hours=total)
        for segment in data.get('worksegments', {}).get('records', []):
            if dates['start_date'] <= (segment.get('timeIn') or '')[:10] <= dates['end_date']:
                if (jobs.get(str(segment.get('jobCodeId')), {}).get('description', '').casefold() == 'tipping'
                        and segment.get('timeIn') == segment.get('timeOut')):
                    continue
                add(store_label(jobs.get(str(segment.get('jobCodeId')), {}).get('locationName')),
                    str(segment['employeeId']))
        for eid, person in catalog.items():
            if any(key[1] == eid for key in rows):
                continue
            if (person.get('hireDate') or '')[:10] > dates['end_date']:
                continue
            if (person.get('terminationDate') or '')[:10] and person['terminationDate'][:10] < dates['start_date']:
                continue
            add(store_label(person.get('location')), eid)
    from store_readiness import review_allocations
    allocation = review_allocations(payroll, report, list(rows.values()), tips, STORES, comparison)
    if allocation_audit is not None:
        allocation_audit.update(allocation)
    allocated = any(row['tips'] is not None for row in rows.values())
    for row in rows.values():
        detail = allocation['stores'].get(row['store'], {})
        row['status'] = ('TCP hours / Gateway tips calculated; ADP import review pending' if detail.get('allocated')
                         else 'Store tip pool unallocated: ' + '; '.join(detail.get('issues', ['Store unassigned'])))
        if row['store'] not in STORES:
            row['status'] += '; store unassigned'
    return list(rows.values()), allocated


def combine(rows):
    result = {}
    for row in rows:
        if row['geid'] not in result:
            result[row['geid']] = {**row, 'roles': list(row['roles'])}
            continue
        target = result[row['geid']]
        target['roles'] = sorted(set(target['roles']) | set(row['roles']))
        if target['status'] != row['status']:
            target['status'] = '; '.join(dict.fromkeys([target['status'], row['status']]))
        for field in ('regular_hours', 'overtime_hours', 'total_hours', 'tips'):
            target[field] = (None if target[field] is None or row[field] is None
                             else f'{Decimal(target[field]) + Decimal(row[field]):.2f}')
    return list(result.values())


def write_review(path, rows, issues, gateway_total=None, issues_only=False, tip_detail=None):
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('Review workbook authoring requires the GitHub runner')
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    book = Workbook()
    sheet = book.active
    sheet.title = 'Payroll review'
    sheet.append(['INCOMPLETE PAYROLL — REVIEW ONLY; NOT AN ADP IMPORT'])
    sheet.append(['Known TCP amounts may exclude missing punches. Blank means unverified, not zero.'])
    if gateway_total is not None:
        sheet.append(['Gateway store tips', float(gateway_total)])
    if tip_detail:
        if tip_detail['allocated']:
            crew_hours = Decimal(tip_detail['eligible_crew_hours'])
            sheet.append(['Eligible crew hours', float(crew_hours)])
            sheet.append(['Tips per eligible crew hour', float(Decimal(tip_detail['gateway_tips']) / crew_hours) if crew_hours else 0])
            sheet.append(['Allocated tips', float(tip_detail['allocated_tips'])])
        else:
            sheet.append(['STORE TIP POOL UNALLOCATED — correct the issues before distributing tips.'])
    for issue in issues:
        sheet.append([issue])
    groups = [
        ('CREW AND CREW LEADERS', [p for p in rows if set(p['roles']) & {'crew', 'crew member', 'crew leader'} and not set(p['roles']) & MANAGEMENT]),
        ('MANAGERS', [p for p in rows if set(p['roles']) & MANAGEMENT]),
        ('UNASSIGNED — REVIEW', [p for p in rows if not set(p['roles']) & (MANAGEMENT | {'crew', 'crew member', 'crew leader'})])]
    for title, group in ([] if issues_only else groups):
        sheet.append([])
        sheet.append([title])
        sheet.append(FIELDS)
        for row in sorted(group, key=lambda p: (p['last_name'].casefold(), p['first_name'].casefold(), p['geid'])):
            sheet.append([row['geid'], (row['first_name'] + ' ' + row['last_name']).strip(), '; '.join(row['roles']),
                          *[None if row[f] is None else float(row[f]) for f in ('regular_hours', 'overtime_hours', 'total_hours', 'tips')], row['status']])
        totals = [None if not group or any(p[f] is None for p in group)
                  else float(sum(Decimal(p[f]) for p in group))
                  for f in ('regular_hours', 'overtime_hours', 'total_hours', 'tips')]
        sheet.append(['KNOWN TOTAL', '', '', *totals, 'Returned amounts only; not final payroll'])
    for row in sheet:
        for cell in row:
            cell.font = Font(name='Arial', size=11)
            if isinstance(cell.value, str):
                cell.data_type = 's'
            elif isinstance(cell.value, (float, int)):
                cell.number_format = '0.00'
    sheet['A1'].font = Font(name='Arial', size=12, bold=True, color='9C0006')
    sheet.sheet_view.showGridLines = False
    for i, width in enumerate([17, 34, 26, 23, 24, 23, 20, 76], 1):
        sheet.column_dimensions[get_column_letter(i)].width = width
    for row in sheet:
        if row[0].value == 'GEID':
            for cell in row:
                cell.fill = PatternFill('solid', fgColor='163C53')
                cell.font = Font(name='Arial', bold=True, color='FFFFFF')
                cell.alignment = Alignment(wrap_text=True)
            sheet.row_dimensions[row[0].row].height = 34
        elif row[0].value and all(c.value is None for c in row[1:]):
            sheet.merge_cells(start_row=row[0].row, start_column=1, end_row=row[0].row, end_column=8)
            row[0].alignment = Alignment(wrap_text=True, vertical='top')
            sheet.row_dimensions[row[0].row].height = max(24, 18 * ((len(str(row[0].value)) // 210) + 1))
        elif row[0].value == 'KNOWN TOTAL':
            for cell in row:
                cell.fill = PatternFill('solid', fgColor='E8EDF2')
                cell.font = Font(name='Arial', bold=True)
        elif row[0].value == 'Tips per eligible crew hour':
            row[1].number_format = '0.000000'
        elif len(row) >= 8 and row[7].value:
            row[7].alignment = Alignment(wrap_text=True, vertical='top')
            sheet.row_dimensions[row[0].row].height = max(34, 15 * ((len(str(row[7].value)) // 72) + 1))
    book.save(path)


def prepare_review(dates, output, issues, payroll=None, report=None, tips=None, comparison=None, revision=''):
    from payroll_run import STORES
    from review_adp import prepare_candidate, issue_notes
    revision = review_revision(revision)
    output.mkdir(parents=True, exist_ok=True)
    allocation = {}
    rows, allocated = review_rows(dates, payroll, report, tips, comparison, allocation)
    label = date_label(dates)
    tip_totals = {p['store']: p['total_tips'] for p in (tips or {}).get('stores', [])}
    comparison = comparison or {}
    messages = []
    csv_audits = {}
    unmapped = [p for p in rows if p['store'] not in STORES]
    corrections = []
    if report:
        try:
            source = summarize(report)
            corrections = [s for s in source['segments'] if s['category'] in ('Open punch', 'Zero-hour shift')]
        except (ValueError, KeyError, TypeError):
            pass
    for prefix, (code, name) in REGIONS.items():
        regional_rows = [p for p in rows if p['store'].startswith(prefix + '-')]
        regional_ids = {p['geid'] for p in regional_rows}
        notes = [issue for issue in issues
                 if not issue.startswith('GEID ') or issue[5:].split(': ', 1)[0] in regional_ids]
        if corrections:
            notes = [issue for issue in notes if not issue.startswith((
                'TCP check requires correction: open_punches', 'TCP check requires correction: zero_hour_closed_shifts'))]
        for correction in corrections:
            if correction['store'].startswith(prefix + '-'):
                notes.append(f'{correction["store"]}: {correction["name"]} (GEID {correction["employee_id"]}), '
                             f'{correction["time_in"]}, segment {correction["segment_id"]}: '
                             f'{correction["category"]}. Verify and correct this timecard in TCP.')
        for store, detail in comparison.get('stores', {}).items():
            if store.startswith(prefix + '-'):
                notes.extend(store + ': ' + issue for issue in detail['issues'])
        for store, detail in allocation['stores'].items():
            if store.startswith(prefix + '-'):
                notes.extend(store + ': ' + issue for issue in detail['issues'])
        notes = list(dict.fromkeys(notes))
        regional = combine(regional_rows)
        csv_filename, csv_audit = prepare_candidate(prefix, regional, dates, output)
        csv_audits[code] = csv_audit
        notes.extend(issue_notes(csv_audit))
        issues_filename = f'{code} Payroll issues {label}.xlsx'
        write_review(output / issues_filename, [], notes, issues_only=True)
        pointer = [f'Source checks and exact timecards to correct: {issues_filename}', f'Period: {label}']
        filename = f'{code} Payroll REVIEW {label}.xlsx'
        write_review(output / filename, regional, pointer)
        files = ([csv_filename] if csv_filename else []) + [filename, issues_filename]
        for store in STORES:
            if store.startswith(prefix + '-'):
                filename = f'{store} Payroll REVIEW {label}.xlsx'
                write_review(output / filename, [p for p in rows if p['store'] == store], pointer, tip_totals.get(store),
                             tip_detail=allocation['stores'][store])
                files.append(filename)
        if unmapped:
            filename = f'Unassigned employees REVIEW {label}.xlsx'
            write_review(output / filename, combine(unmapped), ['Home/work store could not be verified; no region was guessed.'])
            files.append(filename)
        message = EmailMessage()
        message['From'] = 'ab@caazllc.com'
        message['To'] = 'alicia@lccollc.com'
        message['Subject'] = f'{code} TIPS {label} - INCOMPLETE PAYROLL REVIEW'
        suffix = '-' + revision if revision else ''
        message['Message-ID'] = f'<payroll-review-{code}-{dates["start_date"]}-{dates["end_date"]}{suffix}@caazllc.com>'
        if revision:
            message['X-Payroll-Review-Revision'] = revision
        message['X-ADP-Import-Status'] = 'incomplete' if csv_filename else 'blocked'
        totals = {f: sum(Decimal(p[f]) for p in regional_rows if p[f] is not None)
                  for f in ('regular_hours', 'overtime_hours', 'total_hours', 'tips')}
        region_stores = [p for s, p in allocation['stores'].items() if s.startswith(prefix + '-')]
        region_complete = all(p['allocated'] for p in region_stores)
        unallocated = sum(Decimal(p['gateway_tips']) for p in region_stores
                          if not p['allocated'] and p['gateway_tips'] is not None)
        totals_text = (f'Known regular hours: {totals["regular_hours"]:.2f}\n'
                       f'Known overtime hours: {totals["overtime_hours"]:.2f}\n'
                       f'Known total hours: {totals["total_hours"]:.2f}\n'
                       f'{"Total tips" if region_complete else "Allocated tips"}: ${totals["tips"]:.2f}\n')
        if not region_complete:
            totals_text += f'Unallocated known store tip pools: ${unallocated:.2f}\n'
        message.set_content(f'Hi Alicia,\n\nAttached are the {name} payroll review reports for {label}.\n\n'
            + ('Updated review reports; replace the earlier review attachments for this period.\n\n' if revision else '')
            + totals_text + '\n'
            + (f'Included: {csv_filename}. This CSV contains known matched amounts and is incomplete; '
               'review the issues before finalizing payroll. It preserves the last supplied ADP roster. '
               if csv_filename else 'The EPI CSV could not be created from the supplied ADP template; see the issues sheet. ')
            +
            f'See {issues_filename} for source checks and exact timecards to correct. '
            'Blank amounts are unverified, not zero.\n\n'
            + ('Tips are calculated only for stores with complete TCP hours and verified roles. '
               'Affected store pools remain unallocated until their issues are corrected.\n' if allocated
               else 'Employee tips have not been allocated because the payroll inputs are incomplete.\n')
            + '\nCorrected payroll reports must be generated after the issues are resolved.\n')
        for filename in files:
            message.add_attachment((output / filename).read_bytes(),
                maintype='text' if filename.endswith('.CSV') else 'application',
                subtype='csv' if filename.endswith('.CSV') else 'vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                filename=filename)
        (output / (message['Subject'] + '.eml')).write_bytes(message.as_bytes())
        messages.append(message)
    audit = {'period': dates, 'ready': False, 'reports_deliverable': False, 'review_reports_deliverable': True,
             'issues': issues, 'tip_allocation_provisional': allocated, 'source_reconciliation': comparison,
             'store_allocation': allocation, 'review_revision': revision, 'epi_candidates': csv_audits}
    (output / 'review-payroll-rows.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    (output / 'review-readiness.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    return messages


def send_review(messages, end_date, revision=''):
    from daily_report import DeliveryLedger, deliver
    revision = review_revision(revision)
    for message in messages:
        if message.get('X-Payroll-Review-Revision', '') != revision:
            raise ValueError('Review message revision does not match its delivery receipt')
    results = {}
    # A blocked review receipt cannot suppress a later corrected payroll email.
    suffix = '-' + revision if revision else ''
    ledgers = {code: DeliveryLedger(end_date + '-' + code + '-review' + suffix, namespace='payroll')
               for code, _ in REGIONS.values()}
    for ledger in ledgers.values():
        previous = ledger.read()
        if previous and previous.get('status') != 'sent':
            raise ValueError('Uncertain prior payroll review email; check Gmail Sent')
    for message in messages:
        code = message['Subject'].split()[0]
        results[code] = deliver(message, ledgers[code], end_date)
    return results
