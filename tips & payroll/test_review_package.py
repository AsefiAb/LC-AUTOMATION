import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook
import payroll_run
from review_package import combine, prepare_review, review_rows, send_review


class PayrollReviewTests(unittest.TestCase):
    def setUp(self):
        self.dates = {'start_date': '2026-09-22', 'end_date': '2026-10-05'}
        self.payroll = dict(period=self.dates, verified=False, issues=['Missing calculated shift'],
            roster=[dict(geid='1', first_name='Test', last_name='Worker', roles=['crew']),
                    dict(geid='2', first_name='Test', last_name='Manager', roles=['manager'])],
            employees=[dict(store='3356-01', employee_id='1', roles=['crew'], regular_hours='10.00',
                            overtime_hours='1.00', total_hours='11.00'),
                       dict(store='3356-02', employee_id='1', roles=['crew'], regular_hours='3.00',
                            overtime_hours='0.50', total_hours='3.50'),
                       dict(store='3356-01', employee_id='2', roles=['manager'], regular_hours='40.00',
                            overtime_hours='2.00', total_hours='42.00')])
        self.tips = dict(period=self.dates, verified=True, issues=[],
                         stores=[dict(store=s, total_tips='14.50' if s == '3356-01' else '0.00')
                                 for s in payroll_run.STORES])

    def test_current_roster_required_without_suppressing_review(self):
        with patch.dict(os.environ, {'ADP_ROSTER_SETTINGS': json.dumps({'period': {
                'start_date': '2026-09-08', 'end_date': '2026-09-21'}})}, clear=True):
            _, issues = payroll_run.roster_setup(self.dates)
        self.assertEqual(len(issues), 4)
        self.assertIn('older payroll controls cannot be reused', issues[0])
        for text in ('bad-json', '[]'):
            with patch.dict(os.environ, {'ADP_ROSTER_SETTINGS': text}, clear=True):
                self.assertTrue(payroll_run.roster_setup(self.dates)[1])

    def test_missing_calculations_never_allocate_tips(self):
        rows, allocated = review_rows(self.dates, self.payroll, tips=self.tips)
        self.assertFalse(allocated)
        self.assertTrue(all(p['tips'] is None for p in rows))
        combined = next(p for p in combine(rows) if p['geid'] == '1')
        self.assertEqual((combined['regular_hours'], combined['overtime_hours'], combined['total_hours']),
                         ('13.00', '1.50', '14.50'))
        rows[0]['regular_hours'] = None
        self.assertIsNone(next(p for p in combine(rows) if p['geid'] == '1')['regular_hours'])

    def test_verified_hours_only_use_existing_allocator_and_managers_get_zero(self):
        payroll = copy.deepcopy(self.payroll)
        payroll.update(verified=True, issues=[])
        rows, allocated = review_rows(self.dates, payroll, tips=self.tips)
        self.assertTrue(allocated)
        self.assertEqual(next(p for p in rows if p['geid'] == '2')['tips'], '0.00')
        self.assertEqual(next(p for p in rows if p['store'] == '3356-01' and p['geid'] == '1')['tips'], '14.50')

    def test_work_only_person_is_visible_with_blank_hours_not_zero(self):
        report = dict(period=dict(startDate='2026-09-22', stopDate='2026-10-05'), datasets={
            'employees': {'records': []}, 'jobcodes': {'records': [dict(jobCodeId=1, locationName='3910-04')]},
            'worksegments': {'records': [dict(employeeId=99, jobCodeId=1, timeIn='2026-10-01T09:00:00')]}})
        rows, allocated = review_rows(self.dates, report=report)
        self.assertFalse(allocated)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['geid'], rows[0]['store']), ('99', '3910-04'))
        self.assertIsNone(rows[0]['regular_hours'])
        self.assertIsNone(rows[0]['tips'])

    def test_review_attachments_are_native_xlsx_not_stale_imports(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}):
            messages = prepare_review(self.dates, Path(folder), ['Current ADP roster required'], self.payroll, tips=self.tips)
            self.assertEqual(len(messages), 3)
            self.assertEqual([len(list(m.iter_attachments())) for m in messages], [6, 9, 9])
            for message in messages:
                self.assertIn('INCOMPLETE PAYROLL REVIEW', message['Subject'])
                self.assertEqual(message['X-ADP-Import-Status'], 'blocked')
                self.assertTrue(all(a.get_filename().endswith('.xlsx') for a in message.iter_attachments()))
            book = load_workbook(Path(folder) / 'CO Payroll REVIEW 9.22.26-10.5.26.xlsx', data_only=False)
            sheet = book.active
            worker = next(row for row in sheet.values if row[0] == '1')
            self.assertEqual(worker[3:6], (13, 1.5, 14.5))
            self.assertIsNone(worker[6])
            self.assertIn('NOT AN ADP IMPORT', sheet['A1'].value)
            self.assertFalse(json.loads((Path(folder) / 'review-readiness.json').read_text())['ready'])

    def test_different_periods_rejected(self):
        dates = dict(start_date='2026-09-08', end_date='2026-09-21')
        with self.assertRaises(ValueError):
            review_rows(dates, self.payroll)

    def test_review_receipts_do_not_use_final_payroll_paths(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sales automation'))
        from email.message import EmailMessage
        messages = []
        for code in ('TX', 'AZ', 'CO'):
            msg = EmailMessage()
            msg['Subject'] = code + ' TIPS period - INCOMPLETE PAYROLL REVIEW'
            messages.append(msg)
        with patch('daily_report.DeliveryLedger') as ledger, patch('daily_report.deliver', return_value='sent'):
            ledger.return_value.read.return_value = None
            self.assertEqual(set(send_review(messages, '2026-10-05')), {'TX', 'AZ', 'CO'})
            self.assertEqual([call.args[0] for call in ledger.call_args_list],
                             ['2026-10-05-TX-review', '2026-10-05-AZ-review', '2026-10-05-CO-review'])

    def test_blocked_pipeline_prepares_and_sends_only_review_reports(self):
        report = dict(period=dict(startDate='2026-09-22', stopDate='2026-10-05'), datasets={})
        env = {k: 'test' for k in ('TCP_CLIENT_ID', 'TCP_CLIENT_SECRET', 'TCP_API_KEY', 'CAAZ_GMAIL_APP_PASS')}
        env['GITHUB_ACTIONS'] = 'true'
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, env, clear=True), \
                patch('sys.argv', ['payroll_run', '--mode', 'deliver']), \
                patch.object(payroll_run, 'FOLDER', Path(folder)), \
                patch.object(payroll_run, 'period_for', return_value=self.dates), \
                patch.object(payroll_run, 'fetch_tcp', return_value=report), \
                patch.object(payroll_run, 'normalize', return_value=self.payroll), \
                patch.object(payroll_run, 'write_issues'), \
                patch.object(payroll_run.gateway, 'missing_secrets', return_value=[]), \
                patch.object(payroll_run.gateway, 'fetch', return_value=self.tips), \
                patch.object(payroll_run, 'delivery_ledgers') as ledgers, \
                patch.object(payroll_run, 'send_regions') as final_send, \
                patch('review_package.send_review', return_value={'TX': 'sent', 'AZ': 'sent', 'CO': 'sent'}) as review_send:
            ledgers.return_value = {'TX': unittest.mock.Mock(), 'AZ': unittest.mock.Mock(), 'CO': unittest.mock.Mock()}
            for ledger in ledgers.return_value.values():
                ledger.read.return_value = None
            self.assertEqual(payroll_run.main(), 2)
            final_send.assert_not_called()
            review_send.assert_called_once()
            output = Path(folder) / 'reports/2026-09-22_2026-10-05'
            self.assertTrue((output / 'review-delivery-results.json').exists())
            self.assertFalse(json.loads((output / 'readiness.json').read_text())['ready'])
            self.assertEqual(len(list((output / 'REVIEW ONLY').glob('*.xlsx'))), 24)
            self.assertFalse(list(output.rglob('EPI*.CSV')))


if __name__ == '__main__':
    unittest.main()
