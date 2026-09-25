import unittest
from inspect_labor_settings import parse_settings


class LaborSettingsTests(unittest.TestCase):
    def fixture(self):
        return ('3759-01', [['Start of Day'] + ['04:00 AM - Today'] * 7,
                            ['End of Day'] + ['04:00 AM - Tomorrow'] * 7],
                [['Law', 'OT Percentage', '150.00%']], ['$0.00', '0:00', '$0.00'])

    def test_observed_store_settings(self):
        result = parse_settings(*self.fixture())
        self.assertEqual(result['cutoff_hour_local'], 4)
        self.assertEqual(result['overtime1_multiplier'], '1.50')
        self.assertEqual(result['daily_salary'], '0.00')

    def test_incomplete_or_variable_boundaries_rejected(self):
        args = self.fixture()
        args[1][0][-1] = '03:00 AM - Today'
        with self.assertRaises(ValueError):
            parse_settings(*args)

    def test_missing_overtime_rejected(self):
        args = self.fixture()
        args[2].clear()
        with self.assertRaises(ValueError):
            parse_settings(*args)
