import html
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from payroll_email_summary import FIELDS, summarize_rows, read_store_workbooks, build_message


def inputs():
    manifest = {
        'verified': True, 'roster_verified': True, 'cross_store_ot_verified': True, 'issues': [],
        'period': {'start_date': '2026-09-08', 'end_date': '2026-09-21'},
        'expected_stores': ['3356-01', '3356-02'],
        'gateway_tips': {'3356-01': '30.00', '3356-02': '20.00'},
        'roster': [
            {'geid': '100', 'first_name': 'Taylor', 'last_name': 'Manager', 'roles': ['ASST MGR']},
            {'geid': '200', 'first_name': 'Pat', 'last_name': 'Crew', 'roles': ['CREW']},
            {'geid': '300', 'first_name': 'New', 'last_name': 'Employee', 'roles': ['CREW']},
        ],
    }
    rows = [
        {'store': '3356-01', 'geid': '100', 'first_name': 'Taylor', 'last_name': 'Manager', 'roles': 'ASST MGR', 'regular_hours': '78.22', 'overtime_hours': '7.70', 'total_hours': '85.92', 'tips': '0.00'},
        {'store': '3356-02', 'geid': '100', 'first_name': 'Taylor', 'last_name': 'Manager', 'roles': 'ASST MGR', 'regular_hours': '10.00', 'overtime_hours': '2.00', 'total_hours': '12.00', 'tips': '0.00'},
        {'store': '3356-01', 'geid': '200', 'first_name': 'Pat', 'last_name': 'Crew', 'roles': 'CREW', 'regular_hours': '20.00', 'overtime_hours': '0.00', 'total_hours': '20.00', 'tips': '30.00'},
        {'store': '3356-02', 'geid': '200', 'first_name': 'Pat', 'last_name': 'Crew', 'roles': 'CREW', 'regular_hours': '5.00', 'overtime_hours': '1.00', 'total_hours': '6.00', 'tips': '20.00'},
    ]
    return manifest, rows


def workbook_fixture(file, rows, uncached=False):
    """Minimal test fixture only; no real payroll records or output workbooks."""
    def cell(value):
        return f'<c t="inlineStr"><is><t>{html.escape(str(value))}</t></is></c>'
    data = '<row>' + ''.join(cell(v) for v in FIELDS) + '</row>'
    for row in rows:
        data += '<row>' + ''.join('<c><f>1+1</f></c>' if uncached and i == 7 else cell(v) for i, v in enumerate(row)) + '</row>'
    with ZipFile(file, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Payroll" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{data}</sheetData></worksheet>')


class SummaryTests(unittest.TestCase):
    def test_managers_and_crew_combine_by_geid_and_keep_zero_hour_roster(self):
        result = summarize_rows(*inputs())
        self.assertEqual(len(result['employees']), 3)
        manager = result['managers'][0]
        self.assertEqual((manager['regular_hours'], manager['overtime_hours'], manager['total_hours'], manager['tips']), ('88.22', '9.70', '97.92', '0.00'))
        crew = next(r for r in result['employees'] if r['geid'] == '200')
        self.assertEqual((crew['regular_hours'], crew['overtime_hours'], crew['tips']), ('25.00', '1.00', '50.00'))
        zero = next(r for r in result['employees'] if r['geid'] == '300')
        self.assertEqual(zero['total_hours'], '0.00')
        self.assertEqual(result['totals']['total_hours'], '123.92')

    def test_different_people_with_same_name_are_not_merged(self):
        manifest, rows = inputs()
        manifest['roster'][2].update(first_name='Pat', last_name='Crew')
        result = summarize_rows(manifest, rows)
        self.assertEqual(sum(r['last_name'] == 'Crew' for r in result['employees']), 2)

    def test_duplicate_employee_store_rows_stop_instead_of_double_counting(self):
        manifest, rows = inputs()
        with self.assertRaisesRegex(ValueError, 'Duplicate employee row'):
            summarize_rows(manifest, rows + [rows[0]])

    def test_manager_anywhere_in_period_cannot_receive_tips(self):
        manifest, rows = inputs()
        rows[3]['roles'] = 'Manager'
        with self.assertRaisesRegex(ValueError, 'Manager received tips'):
            summarize_rows(manifest, rows)

    def test_hours_are_not_reclassified_by_an_80_hour_threshold(self):
        manifest, rows = inputs()
        rows[0].update(regular_hours='85.00', overtime_hours='0.00', total_hours='85.00')
        result = summarize_rows(manifest, rows)
        self.assertEqual(result['managers'][0]['regular_hours'], '95.00')
        self.assertEqual(result['managers'][0]['overtime_hours'], '2.00')

    def test_unverified_overtime_missing_gateway_or_unknown_roster_blocks(self):
        for change in ('overtime', 'gateway', 'roster'):
            manifest, rows = inputs()
            if change == 'overtime': manifest['cross_store_ot_verified'] = False
            if change == 'gateway': del manifest['gateway_tips']['3356-02']
            if change == 'roster': rows[0]['geid'] = '999'
            with self.assertRaises(ValueError): summarize_rows(manifest, rows)

    def test_hour_mismatches_missing_values_and_name_mismatches_block(self):
        for key, value in [('total_hours', '85.93'), ('regular_hours', None), ('first_name', 'Different')]:
            manifest, rows = inputs()
            rows[0][key] = value
            with self.assertRaises(ValueError): summarize_rows(manifest, rows)

    def test_tip_cents_reconcile_to_each_store(self):
        manifest, rows = inputs()
        rows[2]['tips'] = '29.99'
        with self.assertRaisesRegex(ValueError, 'reconcile'):
            summarize_rows(manifest, rows)

    def test_email_contains_everyone_and_correct_recipient(self):
        result = summarize_rows(*inputs())
        message = build_message(result, [])
        self.assertEqual(message['From'], 'ab@caazllc.com')
        self.assertEqual(message['To'], 'alicia@lccollc.com')
        body = message.get_body(preferencelist=('html',)).get_content()
        for expected in ['Managers — combined across stores', 'All employees and managers', 'Regular Hours', 'Overtime Hours', 'Tips', '88.22', '9.70', 'New', 'Employee']:
            self.assertIn(expected, body)
        self.assertEqual(len(list(message.iter_attachments())), 2)
        self.assertIn('9.8.26-9.21.26', message['Subject'])

    def test_excel_reader_and_uncached_formula(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            manifest, _ = inputs()
            manifest['expected_stores'] = ['3356-01']
            manifest['gateway_tips'] = {'3356-01': '0.00'}
            name = '3356-01 9.8.26-9.21.26.xlsx'
            manifest['workbooks'] = [{'store': '3356-01', 'file': name, 'sheet': 'Payroll'}]
            row = ['100', '', 'Taylor', 'Manager', 'ASST MGR', '85.92', '0.00', '78.22', '7.70']
            workbook_fixture(directory / name, [row])
            rows, sources = read_store_workbooks(manifest, directory)
            result = summarize_rows(manifest, rows)
            self.assertEqual(result['managers'][0]['total_hours'], '85.92')
            self.assertEqual(len(list(build_message(result, sources).iter_attachments())), 3)
            workbook_fixture(directory / name, [row], uncached=True)
            with self.assertRaisesRegex(ValueError, 'changed'):
                build_message(result, sources)
            rows, _ = read_store_workbooks(manifest, directory)
            with self.assertRaises(ValueError): summarize_rows(manifest, rows)

    def test_wrong_period_filename_and_missing_store_workbook_block(self):
        manifest, _ = inputs()
        manifest['workbooks'] = [{'store': '3356-01', 'file': 'wrong.xlsx', 'sheet': 'Payroll'}]
        with self.assertRaisesRegex(ValueError, 'every expected store'):
            read_store_workbooks(manifest, Path('.'))
        manifest['expected_stores'] = ['3356-01']
        with self.assertRaisesRegex(ValueError, 'matching pay period'):
            read_store_workbooks(manifest, Path('.'))


if __name__ == '__main__':
    unittest.main()
