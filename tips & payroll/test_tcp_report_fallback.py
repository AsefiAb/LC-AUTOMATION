import copy
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from tcp_report_fallback import NS, apply_export, read_export


class NativeFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'Complete Payroll.xml'
        self.header = ['Week', '', 'D', 'In', 'In', 'Out', 'Out', 'Job Code', 'Break', 'Rate',
                       'Hours', 'Reg', 'Ovt1', 'Ovt2', 'Comp Reg', 'Comp OT1', 'Comp OT2', 'Day Total']
        self.shift = ['1', '[', 'Mon', '9/14', '05:58 AM', '9/14', '02:55 PM', '123', '',
                      '16.00', '8:57', '3:23', '5:34', '0:00', '0:00', '0:00', '0:00', '8:57']
        self.report = {'period': {'startDate': '2026-09-08', 'stopDate': '2026-09-21'}, 'errors': [],
                       'datasets': {
                           'hours': {'complete': True, 'records': [{'employee': {'employeeId': 101}, 'segments': [
                               {'recordId': 1, 'timeIn': '2026-09-14T05:58:00', 'timeOut': '2026-09-14T14:55:00',
                                'jobCode': {'jobCodeId': 123}, 'hours': {'regular': '0:00', 'overtime1': '0:00', 'overtime2': '0:00'}}]}]},
                           'worksegments': {'complete': True, 'records': [{'id': 1, 'jobCodeId': 123, 'timeIn': '2026-09-14T05:58:00'}]},
                           'jobcodes': {'complete': True, 'records': [{'jobCodeId': 123}]}}}
        self.write()

    def write(self, shifts=None, period='9/14/2026 to 9/14/2026'):
        root = ET.Element(NS + 'Workbook')
        table = ET.SubElement(ET.SubElement(root, NS + 'Worksheet'), NS + 'Table')
        for values in [['Complete Payroll'], [f'For the period of {period}'],
                       ['Name: Test Person     Number: 101'], self.header] + (shifts or [self.shift]):
            row = ET.SubElement(table, NS + 'Row')
            for value in values:
                cell = ET.SubElement(row, NS + 'Cell')
                ET.SubElement(cell, NS + 'Data').text = value
        ET.ElementTree(root).write(self.path, encoding='utf-8', xml_declaration=True)

    def test_restores_native_ot_and_keeps_original_audit(self):
        original = copy.deepcopy(self.report)
        result = apply_export(self.report, self.path)
        self.assertEqual(self.report, original)
        self.assertEqual(result['datasets']['hours']['records'][0]['segments'][0]['hours'],
                         {'regular': '3:23', 'overtime1': '5:34', 'overtime2': '0:00'})
        self.assertEqual(len(result['native_export_reconciliation'][0]['replacements']), 1)

    def test_matching_existing_hours_are_not_replaced(self):
        first = apply_export(self.report, self.path)
        second = apply_export(first, self.path)
        self.assertEqual(second['native_export_reconciliation'][-1]['replacements'], [])

    def test_only_exact_approved_orphan_can_be_reported_separately(self):
        orphan = {'id': '9', 'employeeId': '999', 'jobCodeId': '123',
                  'timeIn': '2026-09-14T09:00:00', 'timeOut': '2026-09-14T14:35:00'}
        self.report['datasets']['employees'] = {'complete': True, 'records': [{'employeeId': 101}]}
        self.report['datasets']['jobcodes']['records'][0]['locationName'] = '03910-00004'
        self.report['datasets']['worksegments']['records'].append(orphan)
        spec = dict(orphan, store='3910-04')
        with self.assertRaisesRegex(ValueError, 'coverage'):
            apply_export(self.report, self.path)
        result = apply_export(self.report, self.path, [spec])
        self.assertEqual(len(self.report['datasets']['worksegments']['records']), 2)
        self.assertEqual(len(result['datasets']['worksegments']['records']), 1)
        self.assertEqual(result['reported_source_exceptions'][0]['id'], '9')
        self.assertNotIn('regular_hours', result['reported_source_exceptions'][0])
        with self.assertRaisesRegex(ValueError, 'changed'):
            apply_export(self.report, self.path, [dict(spec, timeOut='2026-09-14T14:36:00')])
        with self.assertRaisesRegex(ValueError, 'store differs'):
            apply_export(self.report, self.path, [dict(spec, store='3910-03')])
        self.report['datasets']['employees']['records'].append({'employeeId': 999})
        with self.assertRaisesRegex(ValueError, 'absent from roster'):
            apply_export(self.report, self.path, [spec])

    def test_equal_punch_zero_marker_matches_but_elapsed_zero_rejected(self):
        self.shift[6] = self.shift[4]
        self.shift[10:14] = ['0:00'] * 4
        self.report['datasets']['hours']['records'][0]['segments'][0]['timeOut'] = '2026-09-14T05:58:00'
        self.write()
        self.assertEqual(apply_export(self.report, self.path)['native_export_reconciliation'][0]['replacements'], [])
        self.shift[6] = '06:58 AM'
        self.write()
        with self.assertRaises(ValueError):
            read_export(self.path)

    def test_optional_export_code_does_not_change_identity(self):
        text = self.path.read_text().replace('Number: 101', 'Number: 101     Export Code: 123')
        self.path.write_text(text)
        self.assertEqual(apply_export(self.report, self.path)['native_export_reconciliation'][0]['matched_shifts'], 1)

    def test_wrong_employee_job_or_punch_rejected(self):
        for change in ({'timeIn': '2026-09-14T05:59:00'}, {'timeOut': None}, {'jobCode': {'jobCodeId': 124}}):
            report = copy.deepcopy(self.report)
            report['datasets']['hours']['records'][0]['segments'][0].update(change)
            with self.assertRaises(ValueError):
                apply_export(report, self.path)
        self.report['datasets']['hours']['records'][0]['employee']['employeeId'] = 102
        with self.assertRaises(ValueError):
            apply_export(self.report, self.path)

    def test_conflicting_nonzero_hours_rejected(self):
        self.report['datasets']['hours']['records'][0]['segments'][0]['hours']['regular'] = '8:57'
        with self.assertRaisesRegex(ValueError, 'Nonzero'):
            apply_export(self.report, self.path)

    def test_missing_or_duplicate_shifts_rejected(self):
        self.report['datasets']['worksegments']['records'].append({'id': 2, 'jobCodeId': 123, 'timeIn': '2026-09-14T12:00:00'})
        with self.assertRaisesRegex(ValueError, 'coverage'):
            apply_export(self.report, self.path)
        self.write([self.shift, self.shift])
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            read_export(self.path)

    def test_period_and_complete_dataset_required(self):
        self.report['period']['startDate'] = '2026-09-15'
        with self.assertRaisesRegex(ValueError, 'outside'):
            apply_export(self.report, self.path)
        self.report['period']['startDate'] = '2026-09-08'
        self.report['datasets']['hours']['complete'] = False
        with self.assertRaisesRegex(ValueError, 'complete'):
            apply_export(self.report, self.path)

    def test_bad_totals_comp_time_and_open_punch_rejected(self):
        for col, value in ((10, '9:00'), (14, '0:30'), (6, '<<Clocked In>>'), (11, '3:99')):
            shift = self.shift.copy()
            shift[col] = value
            self.write([shift])
            with self.assertRaises(ValueError):
                read_export(self.path)

    def test_overnight_year_boundary_is_unambiguous(self):
        self.shift[2:7] = ['Thu', '12/31', '10:00 PM', '1/1', '06:57 AM']
        self.write(period='12/31/2026 to 12/31/2026')
        key = next(iter(read_export(self.path)['shifts']))
        self.assertEqual(key[2:], ('2026-12-31T22:00:00', '2027-01-01T06:57:00'))

    def test_entities_and_non_utf8_xml_rejected(self):
        for value in (b'<!DOCTYPE Workbook [<!ENTITY x "bad">]>', '<Workbook/>'.encode('utf-16')):
            self.path.write_bytes(value)
            with self.assertRaises(ValueError):
                read_export(self.path)


if __name__ == '__main__':
    unittest.main()
