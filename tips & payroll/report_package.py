"""Current payroll package: native ADP import CSVs plus simple store workbooks."""
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path

from allocate_tips import allocate
from adp_roster import build_imports, FILES
from payroll_email_summary import summarize_rows, LABELS, date_label

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
            'summary': summarize_rows(manifest, rows)}


def store_spec(data, sid):
    rows = sorted((r for r in data['rows'] if r['store'] == sid),
                  key=lambda r: (r['last_name'].casefold(), r['first_name'].casefold(), r['geid']))
    store = next(s for s in data['stores'] if s['store'] == sid)
    values = [[sid, '', '', date_label(data['period']), '', ''],
              ['Gateway tips', '', 'Eligible crew hours', '', 'Tips per crew hour', ''],
              [float(store['gateway_tips']), '', float(store['eligible_crew_hours']), '', None, ''],
              ['Tips go only to crew and crew leaders. Managers receive zero tips.', '', '', '', '', ''],
              STORE_FIELDS]
    formulas = {'E3': '=IF(C3>0,A3/C3,0)'}
    for i, p in enumerate(rows, 6):
        values.append([p['geid'], p['first_name'] + ' ' + p['last_name'], float(p['regular_hours']),
                       float(p['overtime_hours']), None, float(p['tips'])])
        formulas[f'E{i}'] = f'=ROUND(C{i}+D{i},2)'
    last = len(values)
    values.append(['TOTAL', '', None, None, None, None])
    for c in 'CDEF':
        formulas[f'{c}{last + 1}'] = f'=SUM({c}6:{c}{last})' if rows else '=0'
    values.extend([['Hours: TCP calculated regular and overtime. Tips: Gateway store pool.', '', '', '', '', ''],
                   ['Tip share = store tips / eligible crew hours × employee hours; pennies reconcile to the store pool.', '', '', '', '', '']])
    return {'store': sid, 'filename': f'{sid} {date_label(data["period"])}.xlsx',
            'values': values, 'formulas': formulas, 'rows': rows, 'total_row': last + 1}


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
    sheet.freeze_panes = 'C6'
    for i, width in enumerate([14, 36, 22, 21, 22, 17], 1):
        sheet.column_dimensions[get_column_letter(i)].width = width
    for i in range(1, sheet.max_row + 1):
        sheet.row_dimensions[i].height = 22
    for r in [5, spec['total_row']]:
        for cell in sheet[r]:
            cell.fill = PatternFill('solid', fgColor='E8EDF2')
            cell.font = Font(name='Arial', size=11, bold=True)
    for row in sheet.iter_rows(min_row=6, max_row=spec['total_row'], min_col=3):
        for cell in row:
            cell.number_format = '$#,##0.00' if cell.column == 6 else '0.00'
    sheet['A3'].number_format = '$#,##0.00'
    sheet['C3'].number_format = '0.00'
    sheet['E3'].number_format = '0.000000'
    sheet['A1'].font = Font(name='Arial', size=14, bold=True)
    sheet.auto_filter.ref = f'A5:F{spec["total_row"] - 1}'
    book.save(folder / spec['filename'])


def prepare_current(gateway, payroll, output, template_folder, settings=None, write_stores=True):
    data = report_data(gateway, payroll)
    output.mkdir(parents=True, exist_ok=True)
    audit = build_imports(data['summary'], template_folder, output, settings)
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
    if ready_regions:
        from regional_email import build_region_messages
        paths = [output / FILES[region][2] for region in ready_regions] + [output / s['filename'] for s in specs]
        paths.append(output / 'Add to ADP or review for termination.csv')
        sources = [{'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]
        messages = build_region_messages(data['summary'], sources, adp_imports=True, included_regions=ready_regions)
        for message in messages:
            (output / (message['Subject'] + '.eml')).write_bytes(message.as_bytes())
    return messages, data, audit


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
            if [c.value for c in sheet[5]] != STORE_FIELDS:
                raise ValueError('Store columns differ from the approved layout')
            for i, row in enumerate(spec['rows'], 6):
                actual = [c.value for c in sheet[i]]
                if (str(actual[0]), actual[1]) != (row['geid'], row['first_name'] + ' ' + row['last_name']):
                    raise ValueError('Store employee identity mismatch')
                for column, key in [(2, 'regular_hours'), (3, 'overtime_hours'), (5, 'tips')]:
                    if Decimal(str(actual[column])) != Decimal(row[key]):
                        raise ValueError('Store employee hours/tips mismatch')
            if sheet.cell(spec['total_row'], 1).value != 'TOTAL' or sheet.max_row != len(spec['values']):
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
