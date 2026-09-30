"""Current payroll package: native ADP import CSVs plus simple store workbooks."""
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path

from allocate_tips import allocate, cents, decimal_text, MANAGEMENT
from adp_roster import build_imports, FILES
from payroll_email_summary import summarize_rows, LABELS, date_label, regional_people

STORE_FIELDS = ['GEID', 'Employee', 'Regular hours', 'Overtime hours', 'Total hours', 'Tips']


def report_data(gateway, payroll):
    allocation = allocate(gateway, payroll)
    stores = [f'{prefix}-{i:02d}' for prefix, count in [('3356', 7), ('3759', 7), ('3910', 4)] for i in range(1, count + 1)]
    if {s['store'] for s in allocation['stores']} != set(stores):
        raise ValueError('All 18 stores must be reconciled')
    manifest = {'period': payroll['period'], 'verified': True, 'roster_verified': True,
                'cross_store_ot_verified': True, 'issues': [], 'expected_stores': stores,
                'roster': payroll['roster'], 'warnings': payroll.get('warnings', []),
                'gateway_tips': {s['store']: s['gateway_tips'] for s in allocation['stores']}}
    people = {p['geid']: p for p in payroll['roster']}
    hours = {(p['store'], p['employee_id']): p for p in payroll['employees']}
    rows = []
    for item in allocation['employees']:
        person, h = people[item['employee_id']], hours[(item['store'], item['employee_id'])]
        rows.append({'store': item['store'], 'geid': item['employee_id'], 'first_name': person['first_name'],
                     'last_name': person['last_name'], 'roles': item['roles'], 'eligible': item['eligible'],
                     'payroll_id': '', 'regular_hours': h['regular_hours'], 'overtime_hours': h['overtime_hours'],
                     'total_hours': h['total_hours'], 'tips': item['tips']})
    return {'period': payroll['period'], 'rows': rows, 'stores': allocation['stores'],
            'source_exceptions': payroll.get('source_exceptions', []),
            'summary': summarize_rows(manifest, rows)}


def store_spec(data, sid):
    rows = sorted((r for r in data['rows'] if r['store'] == sid),
                  key=lambda r: (r['last_name'].casefold(), r['first_name'].casefold(), r['geid']))
    store = next(s for s in data['stores'] if s['store'] == sid)
    values = [[sid, '', '', date_label(data['period']), '', ''],
              ['Gateway tips', '', 'Eligible crew hours', '', 'Tips per crew hour', ''],
              [float(store['gateway_tips']), '', float(store['eligible_crew_hours']), '', None, ''],
              ['Tips go only to crew and crew leaders. Managers receive zero tips.', '', '', '', '', '']]
    if any(e['store'] == sid for e in data.get('source_exceptions', [])):
        values[3][0] = 'Provisional tips: an unverified shift is excluded. See the regional payroll issues workbook.'
    if not data.get('source_reconciliation', {}).get('stores', {}).get(sid, {'ready': True})['ready']:
        values[3][0] = 'Hours and tips provisional: source reconciliation incomplete. See regional payroll issues.'
    formulas = {'E3': '=IF(C3>0,A3/C3,0)'}
    manager_ids = {r['geid'] for r in data['rows'] if set(r['roles']) & MANAGEMENT}
    crew = [r for r in rows if r['eligible']]
    managers = [r for r in rows if r['geid'] in manager_ids]
    other = [r for r in rows if not r['eligible'] and r['geid'] not in manager_ids]
    groups = [('CREW AND CREW LEADERS', 'CREW TOTAL', crew),
              ('MANAGERS', 'MANAGERS TOTAL', managers)]
    if other:
        groups.append(('UNASSIGNED ROLE — REVIEW', 'UNASSIGNED TOTAL', other))
    employee_rows, header_rows, section_rows, subtotal_rows, ordered = [], [], [], [], []
    for title, total_label, people in groups:
        if section_rows:
            values.append([''] * 6)
        values.append([title, '', '', '', '', ''])
        section_rows.append(len(values))
        values.append(STORE_FIELDS)
        header_rows.append(len(values))
        first = len(values) + 1
        for p in people:
            values.append([p['geid'], p['first_name'] + ' ' + p['last_name'], float(p['regular_hours']),
                           float(p['overtime_hours']), None, float(p['tips'])])
            i = len(values)
            employee_rows.append(i)
            ordered.append(p)
            formulas[f'E{i}'] = f'=ROUND(C{i}+D{i},2)'
        last = len(values)
        values.append([total_label, '', None, None, None, None])
        subtotal_rows.append(len(values))
        for c in 'CDEF':
            formulas[f'{c}{len(values)}'] = f'=SUM({c}{first}:{c}{last})' if people else '=0'
    values.append([''] * 6)
    values.append(['STORE TOTAL', '', None, None, None, None])
    total_row = len(values)
    for c in 'CDEF':
        formulas[f'{c}{total_row}'] = '=' + '+'.join(f'{c}{r}' for r in subtotal_rows)
    values.extend([['Hours: TCP calculated regular and overtime. Tips: Gateway store pool.', '', '', '', '', ''],
                   ['Tip share = store tips / eligible crew hours × employee hours; pennies reconcile to the store pool.', '', '', '', '', '']])
    return {'store': sid, 'filename': f'{sid} {date_label(data["period"])}.xlsx',
            'values': values, 'formulas': formulas, 'rows': ordered, 'total_row': total_row,
            'employee_rows': employee_rows, 'header_rows': header_rows,
            'section_rows': section_rows, 'subtotal_rows': subtotal_rows}


def write_store_on_runner(spec, folder):
    # The bundled artifact authoring runtime is not installed on GitHub-hosted
    # runners. Scheduled reports use the existing openpyxl runtime there only.
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('Use the bundled artifact builder for local workbook authoring')
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    book = Workbook()
    sheet = book.active
    sheet.title = 'Payroll'
    for row in spec['values']:
        sheet.append(row)
    for row in sheet:
        for cell in row:
            cell.font = Font(name='Arial', size=11, color='202B36')
            if isinstance(cell.value, str):
                cell.data_type = 's'
    for cell, formula in spec['formulas'].items():
        sheet[cell] = formula
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = 'C7'
    for i, width in enumerate([14, 36, 22, 21, 22, 17], 1):
        sheet.column_dimensions[get_column_letter(i)].width = width
    for i in range(1, sheet.max_row + 1):
        sheet.row_dimensions[i].height = 22
    for r in spec['header_rows'] + spec['subtotal_rows'] + [spec['total_row']]:
        for cell in sheet[r]:
            cell.fill = PatternFill('solid', fgColor='E8EDF2')
            cell.font = Font(name='Arial', size=11, bold=True)
    for r in spec['section_rows']:
        sheet.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        sheet.cell(r, 1).fill = PatternFill('solid', fgColor='163C53')
        sheet.cell(r, 1).font = Font(name='Arial', size=11, bold=True, color='FFFFFF')
    for r in spec['subtotal_rows'] + [spec['total_row']]:
        sheet.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
    for row in sheet.iter_rows(min_row=6, max_row=spec['total_row'], min_col=3):
        for cell in row:
            cell.number_format = '$#,##0.00' if cell.column == 6 else '0.00'
    sheet['A3'].number_format = '$#,##0.00'
    sheet['C3'].number_format = '0.00'
    sheet['E3'].number_format = '0.000000'
    sheet['A1'].font = Font(name='Arial', size=14, bold=True)
    # A whole-sheet sort/filter could move staff across subtotal boundaries.
    book.save(folder / spec['filename'])


def prepare_current(gateway, payroll, output, template_folder, settings=None, write_stores=True, send_with_issues=True):
    data = report_data(gateway, payroll)
    output.mkdir(parents=True, exist_ok=True)
    audit = build_imports(data['summary'], template_folder, output, settings)
    from source_reconciliation import reconcile
    comparison = reconcile(payroll, [s['store'] for s in data['stores']],
                           (settings or {}).get('source_reconciliation'))
    data['source_reconciliation'] = comparison
    audit['source_reconciliation'] = comparison
    (output / 'source-reconciliation.json').write_text(json.dumps(comparison, indent=2), encoding='utf-8')
    for region, (code, _, filename) in FILES.items():
        exceptions = [e for e in data.get('source_exceptions', []) if e['store'].startswith(region + '-')]
        source_checks = [dict(store=s, reason=reason) for s, check in comparison['stores'].items()
                         if s.startswith(region + '-') for reason in check['issues']]
        if (exceptions or source_checks) and code in audit['regions']:
            detail = audit['regions'][code]
            if detail['ready']:
                review = output / 'NEEDS REVIEW'
                review.mkdir(exist_ok=True)
                (output / filename).replace(review / filename)
            detail['ready'] = False
            detail['source_exceptions'] = exceptions
            detail['source_checks'] = source_checks
            note = f'{code}: source hours are unresolved; payroll and affected store tip shares require review'
            detail['issues'].append(note)
            audit['issues'].append(note)
            audit['ready'] = False
            with (output / 'ADP import checks.txt').open('a', encoding='utf-8') as file:
                file.write('\n' + note + '. Resolve TCP employee and calculated hours before finalizing payroll.\n')
    specs = [store_spec(data, s['store']) for s in data['stores']]
    if write_stores:
        for spec in specs:
            write_store_on_runner(spec, output)
    (output / 'report-data.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
    (output / 'adp-readiness.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    import csv
    with (output / 'Add to ADP or review for termination.csv').open('w', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fieldnames=['region', 'geid', 'name', 'regular_hours', 'overtime_hours', 'tips', 'reason'])
        writer.writeheader()
        writer.writerows(audit['missing'])
    messages = []
    ready_regions = [region for region, (code, _, _) in FILES.items() if audit['regions'].get(code, {}).get('ready')]
    issue_regions = {}
    if send_with_issues:
        for region, (code, _, filename) in FILES.items():
            detail = audit['regions'].get(code)
            if not detail or detail['ready']:
                continue
            proof = issue_reconciliation(data['summary'], region, detail)
            proof['source_exceptions'] = detail.get('source_exceptions', [])
            proof['source_checks'] = detail.get('source_checks', [])
            proof['filename'] = f'{code} Payroll issues {date_label(data["period"])}.xlsx'
            write_issues_on_runner(detail['missing'], proof, output)
            # Keep the native filename, but explicitly disclose its incomplete
            # status in the email and the issues workbook. Never invent ADP IDs.
            if region not in ready_regions:
                ready_regions.append(region)
            issue_regions[code] = proof
    audit['reports_deliverable'] = len(ready_regions) == 3
    audit['issue_reports'] = issue_regions
    (output / 'adp-readiness.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    if ready_regions:
        from regional_email import build_region_messages
        paths = [(output / ('NEEDS REVIEW' if FILES[region][0] in issue_regions else '') / FILES[region][2]) for region in ready_regions]
        paths += [output / s['filename'] for s in specs]
        paths += [output / detail['filename'] for detail in issue_regions.values()]
        paths.append(output / 'Add to ADP or review for termination.csv')
        sources = [{'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]
        messages = build_region_messages(data['summary'], sources, adp_imports=True, included_regions=ready_regions, issue_regions=issue_regions)
        for message in messages:
            (output / (message['Subject'] + '.eml')).write_bytes(message.as_bytes())
    return messages, data, audit


def issue_reconciliation(summary, region, detail):
    people = regional_people(summary)[region]
    missing = detail['missing']
    if len({p['geid'] for p in missing}) != len(missing):
        raise ValueError('Duplicate employee in payroll issues')
    expected = {p['geid']: p for p in people}
    result = {}
    for field in ('regular_hours', 'overtime_hours', 'tips'):
        amount = 0
        for p in missing:
            if p['geid'] not in expected or cents(p[field], field) != cents(expected[p['geid']][field], field):
                raise ValueError('Issue sheet does not agree with verified payroll')
            amount += cents(p[field], field)
        if amount + cents(detail['totals'][field], field) != sum(cents(p[field], field) for p in people):
            raise ValueError('ADP import plus issues do not account for all regional payroll')
        result[field] = decimal_text(amount)
    return result


def write_issues_on_runner(people, proof, output):
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('Issues workbook authoring uses the GitHub runner')
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    book = Workbook()
    sheet = book.active
    sheet.title = 'Payroll issues'
    sheet.append(['ADP corrections needed before import'])
    sheet.append(['These employees are included in store reports but are not matched in the ADP import.'])
    sheet.append(['GEID', 'Employee', 'Regular hours', 'Overtime hours', 'Total hours', 'Tips', 'Issue'])
    for p in people:
        sheet.append([p['geid'], p['name'], float(p['regular_hours']), float(p['overtime_hours']),
                      float(Decimal(p['regular_hours']) + Decimal(p['overtime_hours'])), float(p['tips']), p['reason']])
    sheet.append(['TOTAL', '', float(proof['regular_hours']), float(proof['overtime_hours']),
                  float(Decimal(proof['regular_hours']) + Decimal(proof['overtime_hours'])), float(proof['tips']), ''])
    total_row = sheet.max_row
    unknown_rows = []
    for e in proof.get('source_exceptions', []):
        sheet.append([e['employeeId'], 'Identity unverified', 'Unverified', 'Unverified', 'Unverified', 'Unverified',
                      f"{e['store']} | Record {e['id']} | {e['timeIn']} to {e['timeOut']}. {e['reason']}"])
        unknown_rows.append(sheet.max_row)
    for issue in proof.get('source_checks', []):
        sheet.append(['', 'Source reconciliation', 'Unverified', 'Unverified', 'Unverified', 'Unverified',
                      f"{issue['store']} | {issue['reason']}"])
        unknown_rows.append(sheet.max_row)
    if unknown_rows:
        sheet['A2'] = 'Known missing amounts totalled below. Additional source discrepancies are unverified and may change payroll.'
        sheet.append(['Next steps', 'Reconcile source timecards and employee identities, then correct/recalculate TCP hours. Regenerate payroll and store tips before finalizing.'])
        sheet.append(['Tip impact', 'The affected store tip shares are provisional. Adding eligible crew hours changes shares for that store.'])
    for row in sheet:
        for cell in row:
            cell.font = Font(name='Arial', size=11)
            if isinstance(cell.value, str):
                cell.data_type = 's'
            elif isinstance(cell.value, (int, float)):
                cell.number_format = '$#,##0.00' if cell.column == 6 else '0.00'
    for i, width in enumerate([14, 30, 18, 18, 18, 18, 48], 1):
        sheet.column_dimensions[get_column_letter(i)].width = width
    from openpyxl.styles import Alignment
    for r in unknown_rows:
        sheet.cell(r, 7).alignment = Alignment(wrap_text=True, vertical='top')
        sheet.row_dimensions[r].height = 160
    for r in [3, total_row]:
        for cell in sheet[r]:
            cell.font = Font(name='Arial', size=11, bold=True)
            cell.fill = PatternFill('solid', fgColor='FFF0CC')
    sheet.freeze_panes = 'C4'
    sheet.sheet_view.showGridLines = False
    path = output / proof['filename']
    book.save(path)
    check = load_workbook(path, data_only=True, read_only=True)
    try:
        for r, e in zip(unknown_rows, proof.get('source_exceptions', [])):
            actual = [c.value for c in check.active[r]]
            if actual[:6] != [e['employeeId'], 'Identity unverified'] + ['Unverified'] * 4 or e['id'] not in actual[6]:
                raise ValueError('Unverified shift missing or assigned invented hours/tips')
        offset = len(proof.get('source_exceptions', []))
        for r, issue in zip(unknown_rows[offset:], proof.get('source_checks', [])):
            actual = [c.value for c in check.active[r]]
            if actual[1:6] != ['Source reconciliation'] + ['Unverified'] * 4 or actual[6] != f"{issue['store']} | {issue['reason']}":
                raise ValueError('Source reconciliation issue missing from workbook')
        rows = list(check.active.iter_rows(min_row=4, max_row=3 + len(people), values_only=True))
        if len(rows) != len(people):
            raise ValueError('Incomplete issues workbook')
        for actual, p in zip(rows, people):
            if actual[:2] != (p['geid'], p['name']):
                raise ValueError('Wrong employee on issues workbook')
            for col, field in [(2, 'regular_hours'), (3, 'overtime_hours'), (5, 'tips')]:
                if Decimal(str(actual[col])) != Decimal(p[field]):
                    raise ValueError('Incorrect employee amount on issues workbook')
    finally:
        check.close()


def verify_stores(data, folder):
    from openpyxl import load_workbook
    for store in data['stores']:
        spec = store_spec(data, store['store'])
        book = load_workbook(folder / spec['filename'], read_only=True, data_only=False)
        try:
            if book.sheetnames != ['Payroll']:
                raise ValueError('Store workbook must have one Payroll sheet')
            sheet = book.active
            sheet.calculate_dimension(force=True)
            for i in spec['header_rows']:
                if [c.value for c in sheet[i]] != STORE_FIELDS:
                    raise ValueError('Store columns differ from the approved layout')
            for i, row in zip(spec['employee_rows'], spec['rows']):
                actual = [c.value for c in sheet[i]]
                if (str(actual[0]), actual[1]) != (row['geid'], row['first_name'] + ' ' + row['last_name']):
                    raise ValueError('Store employee identity mismatch')
                for column, key in [(2, 'regular_hours'), (3, 'overtime_hours'), (5, 'tips')]:
                    if Decimal(str(actual[column])) != Decimal(row[key]):
                        raise ValueError('Store employee hours/tips mismatch')
            for i in spec['section_rows'] + spec['subtotal_rows']:
                if sheet.cell(i, 1).value != spec['values'][i - 1][0]:
                    raise ValueError('Store crew/manager section mismatch')
            if sheet.cell(spec['total_row'], 1).value != 'STORE TOTAL' or sheet.max_row != len(spec['values']):
                raise ValueError('Unexpected store employee coverage')
            if Decimal(str(sheet['A3'].value)) != Decimal(store['gateway_tips']):
                raise ValueError('Wrong Gateway pool')
            if Decimal(str(sheet['C3'].value)) != Decimal(store['eligible_crew_hours']):
                raise ValueError('Wrong eligible hours denominator')
            for address, formula in spec['formulas'].items():
                if sheet[address].value != formula:
                    raise ValueError('Incorrect store calculation formula')
        finally:
            book.close()
