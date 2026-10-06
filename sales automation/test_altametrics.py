import unittest
import altametrics


class AltametricsTests(unittest.TestCase):
    def table(self):
        return [['', 'Product Net', 'Trans', '', '', '', '', 'Punched Labor %'],
                ['Actual Total', '$1,234.56', '123', '', '', '', '', '23.45%']]

    def test_known_dashboard_columns(self):
        self.assertEqual(altametrics.parse_tables([self.table()]),
                         {'sales': '1234.56', 'transactions': 123, 'labor_pct': '23.45'})

    def test_missing_duplicate_or_changed_columns_stop(self):
        for tables in ([], [self.table(), self.table()], [[self.table()[1]]]):
            with self.assertRaises(ValueError):
                altametrics.parse_tables(tables)

    def test_blank_amount_is_not_zero(self):
        table = self.table()
        table[1][1] = ''
        with self.assertRaises(ValueError):
            altametrics.parse_tables([table])

    def test_fractional_transactions_rejected(self):
        table = self.table()
        table[1][2] = '10.5'
        with self.assertRaises(ValueError):
            altametrics.parse_tables([table])

    def test_completeness_and_dates(self):
        config = {'stores': [{'store': '3356-01', 'region': 'CO'}]}
        record = {'store': '3356-01', 'date': '2026-09-19', 'sales': '0', 'transactions': 0, 'labor_pct': '0'}
        self.assertIsNone(altametrics.validate(record['date'], [record], config)[0]['labor_pct'])
        for records in ([], [record, record], [{**record, 'date': '2026-09-18'}]):
            with self.assertRaises(ValueError):
                altametrics.validate(record['date'], records, config)

    def test_open_store_zero_labor_is_unverified_not_actual_zero(self):
        config = {'stores': [{'store': '3356-01', 'region': 'CO'}]}
        record = {'store': '3356-01', 'date': '2026-09-23', 'sales': '4808.54', 'transactions': 200, 'labor_pct': '0.00'}
        row, = altametrics.validate(record['date'], [record], config)
        self.assertIsNone(row['labor_pct'])
        self.assertEqual(row['source_labor_pct'], '0.00')
        self.assertEqual(row['labor_status'], 'unverified_zero')
        record['labor_pct'] = '17.81'
        self.assertEqual(altametrics.validate(record['date'], [record], config)[0]['labor_pct'], '17.81')


class CloudCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_security_overlay_is_preserved_in_cloud_mode(self):
        from unittest.mock import Mock, AsyncMock, patch
        page = Mock(url='https://app108b.altametrics.com/ers_webApp/dashboard', frames=[])
        page.locator.return_value.inner_text = AsyncMock(return_value='Altametrics Security Check')
        page.locator.return_value.evaluate_all = AsyncMock()
        with patch.dict('os.environ', {'ALTAMETRICS_AUTH_MODE': 'cloud-legacy'}):
            with self.assertRaisesRegex(RuntimeError, 'interactive'):
                await altametrics.guard(page)
        page.locator.return_value.evaluate_all.assert_not_called()
