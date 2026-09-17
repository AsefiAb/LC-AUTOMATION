"""Update only dated store input cells in existing XLSX files.

Preserve the original ZIP parts (charts, formulas, styles, drawings, links).
No guessed next-empty row, blanket worksheet rewrite, or overwrite of conflicts.
"""
import io
import posixpath
import re
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from lxml import etree as ET

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
S = '{' + NS['s'] + '}'


class WorkbookError(RuntimeError):
    pass


def xml(data):
    return ET.fromstring(data, parser=ET.XMLParser(resolve_entities=False, no_network=True))


def serialize(root):
    return ET.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def cell_value(cell, strings):
    if cell is None:
        return ''
    if cell.get('t') == 'inlineStr':
        return ''.join(cell.xpath('.//s:t/text()', namespaces=NS))
    val = cell.find(S + 'v')
    value = val.text if val is not None and val.text is not None else ''
    return strings[int(value)] if cell.get('t') == 's' and value else value


def is_date(value, target, date1904=False):
    text = str(value).strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y', '%m.%d.%y', '%m.%d.%Y'):
        from datetime import datetime
        try:
            if datetime.strptime(text, fmt).date() == target:
                return True
        except ValueError:
            pass
    # Only plausible modern Excel serials, with exact whole-date matching.
    if re.fullmatch(r'\d{5}(?:\.0+)?', text):
        epoch = date(1904, 1, 1) if date1904 else date(1899, 12, 30)
        return epoch + timedelta(days=int(Decimal(text))) == target
    return False


def header_matches(value, store, index):
    text = str(value).strip().lower()
    return text in {str(index), f'store {index}', f'store #{index}', store,
                    f'0{store.split("-")[0]}-{index:05d}', f'#{index}'}


def plan_sheet(root, strings, stores, target, date1904):
    rows = root.findall('.//' + S + 'sheetData/' + S + 'row')
    columns = [chr(ord('E') + i) for i in range(len(stores))]
    by_row = {int(row.get('r')): {c.get('r'): c for c in row.findall(S + 'c')} for row in rows}
    header_rows = []
    date_rows = []
    for row_id, cells in by_row.items():
        if row_id <= 60 and all(header_matches(cell_value(cells.get(f'{col}{row_id}'), strings), store, i + 1)
                                for i, (col, store) in enumerate(zip(columns, stores))):
            header_rows.append(row_id)
        if any(is_date(cell_value(cells.get(f'{col}{row_id}'), strings), target, date1904)
               for col in 'ABCD'):
            date_rows.append(row_id)
    if len(header_rows) != 1:
        raise WorkbookError('Could not verify a unique store-header row in columns E onward.')
    if len(date_rows) != 1 or date_rows[0] <= header_rows[0]:
        raise WorkbookError('Could not identify a unique report-date row below the headers.')
    row_id = date_rows[0]
    return [(store, f'{col}{row_id}', by_row[row_id].get(f'{col}{row_id}'))
            for store, col in zip(stores, columns)]


def update(content, region_rows, report_date):
    target = date.fromisoformat(report_date)
    values = {row['store']: row for row in region_rows}
    stores = sorted(values)
    if len(values) != len(region_rows) or not stores:
        raise WorkbookError('Duplicate or empty store data.')
    source = zipfile.ZipFile(io.BytesIO(content))
    if sum(info.file_size for info in source.infolist()) > 100_000_000:
        raise WorkbookError('Workbook expands beyond the supported size.')
    workbook = xml(source.read('xl/workbook.xml'))
    properties = workbook.find(S + 'workbookPr')
    date1904 = properties is not None and properties.get('date1904') in ('1', 'true')
    relationships = {el.get('Id'): el.get('Target') for el in xml(source.read('xl/_rels/workbook.xml.rels'))}
    strings = []
    if 'xl/sharedStrings.xml' in source.namelist():
        strings = [''.join(el.xpath('.//s:t/text()', namespaces=NS))
                   for el in xml(source.read('xl/sharedStrings.xml'))]
    sheets = {}
    for sheet in workbook.find(S + 'sheets'):
        name = sheet.get('name').upper()
        if name in ('SALES', 'TRANS'):
            dest = relationships[sheet.get('{' + NS['r'] + '}id')]
            path = dest.lstrip('/') if dest.startswith('/') else posixpath.normpath('xl/' + dest)
            if name in sheets:
                raise WorkbookError('Ambiguous sales or transaction worksheet names.')
            sheets[name] = (path, xml(source.read(path)))
    if set(sheets) != {'SALES', 'TRANS'}:
        raise WorkbookError('Workbook must contain SALES and Trans worksheets.')
    changes, plans = [], []
    # Validate both sheets entirely before changing either.
    for name, (path, root) in sheets.items():
        metric = 'sales' if name == 'SALES' else 'transactions'
        for store, address, cell in plan_sheet(root, strings, stores, target, date1904):
            desired = Decimal(str(values[store][metric]))
            if cell is not None and cell.find(S + 'f') is not None:
                raise WorkbookError('Refusing to overwrite a formula in ' + name + '!' + address)
            old = cell_value(cell, strings)
            if old != '':
                try:
                    agrees = Decimal(old) == desired
                except Exception:
                    agrees = False
                if not agrees:
                    raise WorkbookError('Existing value conflicts with source at ' + name + '!' + address)
                continue
            plans.append((root, address, cell, desired))
            changes.append({'sheet': name, 'cell': address, 'store': store, 'value': str(desired)})
    for root, address, cell, desired in plans:
        if cell is None:
            row_num = re.search(r'\d+$', address)[0]
            row = root.find(f'.//{S}sheetData/{S}row[@r="{row_num}"]')
            cell = ET.Element(S + 'c', r=address)
            # Insert in column order, keeping existing cell styles and structure intact.
            def order(ref):
                name = re.match(r'[A-Z]+', ref)[0]
                return len(name), name
            successors = [el for el in row if el.tag == S + 'c' and order(el.get('r')) > order(address)]
            if successors:
                row.insert(row.index(successors[0]), cell)
            else:
                row.append(cell)
        cell.set('t', 'n')
        for child in list(cell):
            if child.tag in (S + 'v', S + 'is'):
                cell.remove(child)
        ET.SubElement(cell, S + 'v').text = str(desired)
    replacements = {path: serialize(root) for path, root in sheets.values()}
    calc = workbook.find(S + 'calcPr')
    if calc is None:
        calc = ET.Element(S + 'calcPr')
        # calcPr belongs before later workbook elements, especially extLst.
        later_tags = {S + tag for tag in ('oleSize', 'customWorkbookViews', 'pivotCaches',
            'smartTagPr', 'smartTagTypes', 'webPublishing', 'fileRecoveryPr',
            'webPublishObjects', 'extLst')}
        later = [element for element in workbook if element.tag in later_tags]
        if later:
            workbook.insert(workbook.index(later[0]), calc)
        else:
            workbook.append(calc)
    calc.set('fullCalcOnLoad', '1')
    calc.set('forceFullCalc', '1')
    replacements['xl/workbook.xml'] = serialize(workbook)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as dest:
        for info in source.infolist():
            dest.writestr(info, replacements.get(info.filename, source.read(info.filename)))
    source.close()
    return output.getvalue(), changes
