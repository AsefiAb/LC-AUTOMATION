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
            messages, summary = p.prepare_legacy(tips, payroll, Path(folder))
            self.assertEqual(len(list(Path(folder).glob('*.xlsx'))), 21)
            self.assertEqual(summary['managers'][0]['total_hours'], '85.92')
            self.assertEqual(summary['managers'][0]['overtime_hours'], '7.70')
            self.assertEqual(summary['managers'][0]['tips'], '0.00')
            self.assertEqual(summary['totals']['tips'], '76.63')
            self.assertEqual(len(summary['employees']), 3)
            for message, code, region, count in zip(messages, ['TX', 'AZ', 'CO'], ['3910', '3759', '3356'], [5, 8, 8]):
                self.assertEqual(message['To'], 'alicia@lccollc.com')
                self.assertEqual(message['Subject'], f'{code} TIPS 8.25.26-9.7.26')
                attachments = [a.get_filename() for a in message.iter_attachments()]
                self.assertEqual(len(attachments), count)
                self.assertEqual(attachments[0], f'{code} TIPS 8.25.26-9.7.26.xlsx')
                self.assertTrue(all(name.startswith(region + '-') for name in attachments[1:]))
                regional = load_workbook(Path(folder) / attachments[0])
                self.assertEqual(regional.sheetnames, [region])
                regional.close()
            book = load_workbook(Path(folder) / '3356-01 8.25.26-9.7.26.xlsx')
            self.assertTrue(any(c.value == '=No' and c.data_type == 's' for row in book['Payroll'] for c in row))
            book.close()
            combined = load_workbook(Path(folder) / 'CO TIPS 8.25.26-9.7.26.xlsx')
            self.assertEqual(combined.sheetnames, ['3356'])
            self.assertEqual([c.value for c in combined['3356'][1]], p.FIELDS)
            self.assertNotIn('Stores worked', [c.value for c in combined['3356'][1]])
            manager = next(row for row in combined['3356'].iter_rows(values_only=True) if row[0] == '102')
            self.assertEqual(manager[5:9], (85.92, 0, 78.22, 7.7))
            combined.close()

    def test_regional_people_does_not_mix_cross_region_hours_or_tips(self):
        from payroll_email_summary import regional_people
        summary = {'employees': [{'geid': '101', 'first_name': 'Test', 'last_name': 'Person',
                                 'job_title': 'CREW', 'regular_hours': '60.00', 'tips': '100.00'}],
                   'source_rows': [
                       {'geid': '101', 'store': '3356-01', 'regular_hours': '10.00', 'overtime_hours': '1.00', 'tips': '25.00'},
                       {'geid': '101', 'store': '3356-02', 'regular_hours': '20.00', 'overtime_hours': '2.00', 'tips': '15.00'},
                       {'geid': '101', 'store': '3910-01', 'regular_hours': '30.00', 'overtime_hours': '0.00', 'tips': '60.00'}]}
        regions = regional_people(summary)
        self.assertEqual(len(regions['3356']), 1)
        self.assertEqual((regions['3356'][0]['regular_hours'], regions['3356'][0]['overtime_hours'], regions['3356'][0]['tips']), ('30.00', '3.00', '40.00'))
        self.assertEqual((regions['3910'][0]['total_hours'], regions['3910'][0]['tips']), ('30.00', '60.00'))
        self.assertEqual(regions['3759'], [])

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
            result = p.normalize(report)
            self.assertEqual(result['roster'][0]['roles'], ['unassigned'])
            self.assertEqual(result['employees'][0]['total_hours'], '0.00')
            self.assertTrue(result['warnings'])
            report['datasets']['employees']['records'][0]['location'] = None
            result = p.normalize(report)
            self.assertEqual(len(result['roster']), 1)
            self.assertEqual(result['employees'], [])

    def test_unassigned_zero_people_retained_but_never_paid(self):
        from allocate_tips import allocate
        from payroll_email_summary import summarize_rows
        dates = {'start_date': '2026-09-08', 'end_date': '2026-09-21'}
        payroll = {'period': dates, 'verified': True, 'issues': [], 'employees': [
            {'store': '3356-01', 'employee_id': '101', 'roles': ['unassigned'], 'total_hours': '0.00'}]}
        tips = {'period': dates, 'verified': True, 'issues': [], 'stores': [{'store': '3356-01', 'total_tips': '0.00'}]}
        result = allocate(tips, payroll)
        self.assertFalse(result['employees'][0]['eligible'])
        self.assertEqual(result['employees'][0]['tips'], '0.00')
        payroll['employees'][0]['total_hours'] = '0.01'
        with self.assertRaises(ValueError):
            allocate(tips, payroll)
        manifest = {'period': dates, 'verified': True, 'roster_verified': True, 'cross_store_ot_verified': True,
                    'expected_stores': ['3356-01'], 'gateway_tips': {'3356-01': '0.00'}, 'roster': [
                        {'geid': '101', 'first_name': 'No', 'last_name': 'Assignment', 'roles': ['unassigned']}]}
        self.assertEqual(len(summarize_rows(manifest, [])['employees']), 1)
        with self.assertRaisesRegex(ValueError, 'Unassigned employee has hours'):
            summarize_rows(manifest, [{'store': '3356-01', 'geid': '101', 'first_name': 'No', 'last_name': 'Assignment',
                'roles': ['unassigned'], 'regular_hours': '0.01', 'overtime_hours': '0.00', 'total_hours': '0.01', 'tips': '0.00'}])

    def test_unique_active_assignment_without_default(self):
        report = self.fixture()
        report['datasets']['employeejobcodes']['records'][0].update(isDefault=False, isActive=True, location='03356-00001')
        report['datasets']['employees']['records'][0].pop('location')
        with patch.object(p, 'STORES', ['3356-01']):
            result = p.normalize(report)
        self.assertEqual(len(result['roster']), 1)
        self.assertEqual(result['employees'][0]['store'], '3356-01')

    def test_zero_duration_tipping_marker_does_not_replace_default_role(self):
        from summarize_inspection import summarize
        report = self.fixture()
        report['datasets']['jobcodes']['records'].append({'jobCodeId': 1002, 'description': 'Tipping'})
        segment = dict(recordId=9, timeIn='2026-08-25T09:00:00', timeOut='2026-08-25T09:00:00',
                       jobCode={'jobCodeId': 1002}, hours=dict(regular='0:00', overtime1='0:00', overtime2='0:00'))
        report['datasets']['hours']['records'] = [dict(employee={'employeeId': 101}, segments=[segment])]
        report['datasets']['worksegments']['records'] = [dict(id=9, employeeId=101, jobCodeId=1002,
            timeIn=segment['timeIn'], timeOut=segment['timeOut'], tracked1='10.00')]
        with patch.object(p, 'STORES', ['3356-01']):
            result = p.normalize(report)
        self.assertEqual(result['roster'][0]['roles'], ['crew leader'])
        self.assertEqual(result['employees'][0]['total_hours'], '0.00')
        summary = summarize(report)
        self.assertEqual(summary['segments'][0]['category'], 'Non-work adjustment')
        self.assertFalse(summary['checks']['hours_only_ids'])
        self.assertFalse(summary['checks']['work_only_ids'])
        # Positive-duration or nonzero-hour tipping records are not silently
        # ignored: they remain a worked-role/source inconsistency.
        segment['timeOut'] = '2026-08-25T09:01:00'
        with patch.object(p, 'STORES', ['3356-01']):
            result = p.normalize(report)
        self.assertTrue(any('Unclassified role: Tipping' in issue for issue in result['issues']))

    def test_hundredths_are_decimal_not_minutes(self):
        self.assertEqual(p.hour_text('1328'), '22.13')
        self.assertEqual(p.hour_text('462'), '7.70')
