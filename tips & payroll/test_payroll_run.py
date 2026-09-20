import copy
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from openpyxl import load_workbook
import gateway
import payroll_run as p


class PayrollPipelineTests(unittest.TestCase):
    def test_period_anchor_and_dst(self):
        for day, expected in [('2026-09-22T15:00:00', ('2026-09-08', '2026-09-21')),
                              ('2026-10-06T15:00:00', ('2026-09-22', '2026-10-05')),
                              ('2026-11-03T16:00:00', ('2026-10-20', '2026-11-02'))]:
            now = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
            self.assertTrue(p.schedule_due(now))
            self.assertEqual(tuple(p.period_for(now).values()), expected)
        self.assertFalse(p.schedule_due(datetime(2026, 9, 29, 15, tzinfo=timezone.utc)))
        self.assertFalse(p.schedule_due(datetime(2026, 11, 3, 15, tzinfo=timezone.utc)))

    def test_incomplete_period_rejected(self):
        with self.assertRaises(ValueError):
            p.period_for(datetime(2026, 9, 20, tzinfo=timezone.utc), '2026-09-08', '2026-09-21')

    def test_gateway_missing_blank_duplicate_are_not_zero(self):
        rows = [[gateway.LABELS[0], '$700.00', ''], [gateway.LABELS[1], '66.36', '']]
        self.assertEqual(gateway.parse_tip_rows([rows])['total_tips'], '766.36')
        for invalid in ([rows[:1]], [rows, rows], [[[gateway.LABELS[0], '', ''], rows[1]]]):
            with self.assertRaises(ValueError):
                gateway.parse_tip_rows(invalid)

    def test_store_workbooks_reconcile_and_combine_manager(self):
        dates = {'start_date': '2026-08-25', 'end_date': '2026-09-07'}
        roster = [{'geid': '101', 'first_name': 'Crew', 'last_name': 'Leader', 'roles': ['crew leader']},
                  {'geid': '102', 'first_name': 'Store', 'last_name': 'Manager', 'roles': ['manager']},
                  {'geid': '103', 'first_name': '=No', 'last_name': 'Hours', 'roles': ['crew']}]
        employees = []
        for store, eid, reg, ot in [('3356-01', '101', '20.00', '2.13'), ('3356-01', '102', '40.00', '0.00'),
                                     ('3356-02', '102', '38.22', '7.70'), ('3356-01', '103', '0.00', '0.00')]:
            person = next(r for r in roster if r['geid'] == eid)
            employees.append({'store': store, 'employee_id': eid, 'name': person['first_name'] + ' ' + person['last_name'],
                              'roles': person['roles'], 'regular_hours': reg, 'overtime_hours': ot,
                              'total_hours': f'{p.Decimal(reg) + p.Decimal(ot):.2f}'})
        payroll = {'period': dates, 'verified': True, 'issues': [], 'roster': roster, 'employees': employees}
        tips = {'period': dates, 'verified': True, 'issues': [],
                'stores': [{'store': s, 'total_tips': '76.63' if s == '3356-01' else '0.00'} for s in p.STORES]}
        with tempfile.TemporaryDirectory() as folder:
            message, summary = p.prepare(tips, payroll, Path(folder))
            self.assertEqual(len(list(Path(folder).glob('*.xlsx'))), 19)
            self.assertEqual(summary['managers'][0]['total_hours'], '85.92')
            self.assertEqual(summary['managers'][0]['overtime_hours'], '7.70')
            self.assertEqual(summary['managers'][0]['tips'], '0.00')
            self.assertEqual(summary['totals']['tips'], '76.63')
            self.assertEqual(len(summary['employees']), 3)
            self.assertEqual(message['To'], 'alicia@lccollc.com')
            book = load_workbook(Path(folder) / '3356-01 8.25.26-9.7.26.xlsx')
            self.assertTrue(any(c.value == '=No' and c.data_type == 's' for row in book['Payroll'] for c in row))
            book.close()

    def fixture(self):
        employee = {'employeeId': 101, 'firstName': 'Crew', 'lastName': 'Leader', 'location': '03356-00001'}
        data = {'locations': [{'name': '03356-00001'}], 'jobcodes': [{'jobCodeId': 2, 'description': 'Crew Leader - 03356-00001'}],
                'employees': [employee], 'hours': [], 'worksegments': [],
                'employeejobcodes': [{'employeeId': 101, 'jobCodeId': 2, 'isDefault': True}]}
        return {'period': {'startDate': '2026-08-25', 'stopDate': '2026-09-07'}, 'errors': {}, 'generated_at': 'test',
                'datasets': {k: {'complete': True, 'records': v} for k, v in data.items()}}

    def test_zero_hour_roster_and_unknown_roles(self):
        report = self.fixture()
        with patch.object(p, 'STORES', ['3356-01']):
            result = p.normalize(report)
            self.assertEqual(len(result['roster']), 1)
            self.assertTrue(all('No calculated worked shifts' in i for i in result['issues']))
            self.assertEqual(result['employees'][0]['total_hours'], '0.00')
            self.assertEqual(result['roster'][0]['roles'], ['crew leader'])
            report['datasets']['employeejobcodes']['records'] = []
            self.assertFalse(p.normalize(report)['verified'])

    def test_unique_active_assignment_without_default(self):
        report = self.fixture()
        report['datasets']['employeejobcodes']['records'][0].update(isDefault=False, isActive=True, location='03356-00001')
        report['datasets']['employees']['records'][0].pop('location')
        with patch.object(p, 'STORES', ['3356-01']):
            result = p.normalize(report)
        self.assertEqual(len(result['roster']), 1)
        self.assertEqual(result['employees'][0]['store'], '3356-01')

    def test_hundredths_are_decimal_not_minutes(self):
        self.assertEqual(p.hour_text('1328'), '22.13')
        self.assertEqual(p.hour_text('462'), '7.70')
