import os
import unittest
from datetime import date
from unittest.mock import patch

from gateway import financial_evidence, missing_secrets, selected_stores
from gateway_financial_inspect import validate_request


class FinancialInspectTests(unittest.TestCase):
    def setUp(self):
        self.dates = dict(start_date='2026-10-05', end_date='2026-10-05')
        self.parameters = dict(start='10/05/2026', end='10/05/2026', store='00003')
        self.tables = [{'caption': '', 'rows': [{'cells': [{'tag': 'TH', 'text': '2026-10-05', 'col_span': 1, 'row_span': 1}]}]}]

    def evidence(self, text, parameters=None):
        return financial_evidence('3356-03', self.dates, parameters or self.parameters, self.tables, text)

    def test_one_store_requires_only_its_region_and_default_remains_all_stores(self):
        self.assertEqual(selected_stores(['3356-03']), {'CO': [3]})
        self.assertEqual(sum(map(len, selected_stores().values())), 18)
        with patch.dict(os.environ, {'CO_GATEWAY_USER': 'test', 'CO_GATEWAY_PASS': 'test'}, clear=True):
            self.assertEqual(missing_secrets(['CO']), [])
            self.assertEqual(len(missing_secrets()), 4)
        for invalid in ([], ['3356-03', '3356-03'], ['3356-08'], ['3356-3'], ['../../file'], '3356-03'):
            with self.assertRaises(ValueError):
                selected_stores(invalid)

    def test_capture_preserves_headers_and_values_without_sales_inference(self):
        text = 'Sales Entry Form - Financial View\n03356-00003\n2026-10-05\nRetrieved: 2026-10-06 01:32 PM UTC\nNet Sales - Taxable (+)\n$0.00'
        capture = self.evidence(text)
        self.assertTrue(capture['source_verified'])
        self.assertEqual(capture['rendered_dates'], ['2026-10-05'])
        self.assertEqual(capture['tables'], self.tables)
        self.assertEqual(capture['visible_text'], text)
        self.assertNotIn('closed', capture)
        self.assertNotIn('sales', capture)

    def test_selected_store_is_not_proof_of_rendered_store(self):
        capture = self.evidence('Sales Entry Form - Financial View\n03356-00002\n2026-10-05')
        self.assertFalse(capture['source_verified'])
        self.assertFalse(capture['checks']['rendered_store_matches'])
        self.assertTrue(capture['checks']['store_control_matches'])

    def test_stale_extra_missing_or_retrieved_dates_are_not_report_proof(self):
        for text in ('03356-00003\n2026-10-04', '03356-00003\n2026-10-04\n2026-10-05',
                     '03356-00003', '03356-00003\nRetrieved: 2026-10-05 08:00 AM UTC'):
            self.assertFalse(self.evidence(text)['source_verified'])
        parameters = {**self.parameters, 'store': '00002'}
        self.assertFalse(self.evidence('03356-00003\n2026-10-05', parameters)['source_verified'])

    def test_dates_and_store_are_bounded_before_browser_access(self):
        dates, region = validate_request('3356-03', '2026-10-05', '2026-10-05', date(2026, 10, 6))
        self.assertEqual((dates, region), (self.dates, 'CO'))
        for first, last in [('2026-10-06', '2026-10-06'), ('2026-10-05', '2026-10-04'),
                            ('2026-08-01', '2026-10-05'), ('2025-01-01', '2025-01-01'), ('invalid', '2026-10-05')]:
            with self.assertRaises(ValueError):
                validate_request('3356-03', first, last, date(2026, 10, 6))


if __name__ == '__main__':
    unittest.main()
