import copy
import unittest
from test_sales import labor_fixture
import tcp_labor
import daily_report


class AltametricsFormulaTests(unittest.TestCase):
    def settings(self):
        return {'3356-01': {'overtime1_multiplier': '1.5', 'daily_salary': '0', 'daily_accrual': '0'}}

    def test_manager_and_overtime_included(self):
        args = labor_fixture()
        args[3]['1']['description'] = 'Manager - 3356-01'
        result = tcp_labor.summarize(*args, cutoff_hour=4, settings=self.settings())
        row = result['stores'][0]
        self.assertEqual(row['base_wage_estimate'], '80.00')
        self.assertEqual(row['wage_estimate'], '85.00')  # 3.5*20 + .5*20*1.5
        rows = [{'store': '3356-01', 'report_date': args[0], 'sales': '1000'}]
        report = daily_report.apply_tcp_estimate(args[0], rows, result)[0]
        self.assertEqual(report['labor_pct'], '8.50')
        self.assertEqual(report['labor_source'], 'TCP wage estimate')

    def test_early_next_day_shift_is_included_once_and_four_am_excluded(self):
        args = labor_fixture()
        args[1][0]['segments'].clear()
        args[2].clear()
        for index, time in enumerate(('2026-09-16T03:59:00', '2026-09-16T04:00:00',
                                     '2026-09-17T03:59:00', '2026-09-17T04:00:00'), 1):
            shift = copy.deepcopy(labor_fixture()[1][0]['segments'][0])
            shift.update(recordId=index, timeIn=time, timeOut='2026-09-17T15:00:00')
            args[1][0]['segments'].append(shift)
            args[2].append({'id': index, 'timeIn': time})
        result = tcp_labor.summarize(*args, cutoff_hour=4, settings=self.settings())
        self.assertTrue(result['complete'])
        self.assertEqual(result['stores'][0]['segments'], 2)
        self.assertEqual(result['stores'][0]['wage_estimate'], '170.00')

    def test_unknown_double_time_salary_or_accrual_does_not_guess(self):
        for kind in ('dt', 'salary', 'accrual'):
            args = labor_fixture()
            settings = self.settings()
            if kind == 'dt':
                args[1][0]['segments'][0]['hours']['overtime2'] = '1:00'
            else:
                settings['3356-01']['daily_' + kind] = '100'
            result = tcp_labor.summarize(*args, cutoff_hour=4, settings=settings)
            self.assertFalse(result['stores'][0]['complete'])
            self.assertIsNone(result['stores'][0]['wage_estimate'])

    def test_bad_offset_is_not_silently_assigned_to_wrong_day(self):
        with self.assertRaises(ValueError):
            tcp_labor.business_date('2026-09-17T03:59:00+00:00', 4)

    def test_email_describes_all_staff_formula(self):
        from test_sales import CONFIG
        args = labor_fixture()
        labor = tcp_labor.summarize(*args, cutoff_hour=4, settings=self.settings())
        rows = [{**CONFIG['stores'][0], 'report_date': args[0], 'sales': '1000', 'transactions': 10}]
        rows = daily_report.apply_tcp_estimate(args[0], rows, labor)
        body = daily_report.email_message(args[0], rows, [], CONFIG).get_body().get_content()
        self.assertIn('Labor 8.50% | Goal', body)
        self.assertNotIn('(TCP estimate)', body)
        self.assertIn('TCP recorded regular and overtime wages', body)
        self.assertIn('Includes crew and management', body)
        self.assertIn('04:00', body)
        self.assertNotIn('Excludes overtime premiums', body)
