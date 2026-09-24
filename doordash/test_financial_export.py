import csv
import tempfile
import unittest
from pathlib import Path

from financial_export import FIELDS, read_exports, reconcile, validate_reconciliation


class FinancialExportTests(unittest.TestCase):
    def record(self, tx='charge', kind='Error Charge', amount='-10.00', adjustment='NULL', store='123'):
        row = dict.fromkeys(FIELDS, '')
        row.update({'Store ID': store, 'Delivery UUID': 'delivery', 'DoorDash transaction ID': tx,
                    'Transaction type': kind, 'Error charges': amount, 'Adjustments': adjustment})
        return row

    def load(self, rows, twice=False):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'export.csv'
            with file.open('w', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            return read_exports([file, file] if twice else [file])

    def ledger(self):
        return {'version': 1, 'orders': {'order': {'storeId': '123', 'store': 'CO-1',
                'status': 'observed', 'href': 'https://www.doordash.com/merchant/financials/transactions/delivery?store_id=123'}}}

    def test_partial_refund_and_reversal_keep_sign_and_do_not_bill(self):
        rows, _, _ = self.load([self.record(), self.record('a', 'Adjustment', 'NULL', '4.00'),
                               self.record('b', 'Adjustment', 'NULL', '-1.00')])
        result = reconcile(rows, self.ledger())
        self.assertEqual(result['matched_orders'], 1)
        self.assertEqual(result['known_net_adjustment_cents'], 300)
        self.assertFalse(result['billing_ready'])
        self.assertNotIn('ab_cut', result)

    def test_overlapping_exports_are_deduplicated(self):
        rows, _, duplicates = self.load([self.record()], twice=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(duplicates, 1)

    def test_conflicting_transaction_cannot_replace_amount(self):
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            self.load([self.record(), self.record(amount='-20.00')])

    def test_store_boundary_prevents_wrong_order_match(self):
        rows, _, _ = self.load([self.record(store='999')])
        result = reconcile(rows, self.ledger())
        self.assertEqual(result['matched_orders'], 0)
        self.assertEqual(result['other_store_ids'], ['999'])

    def test_missing_amount_is_not_verified_zero(self):
        rows, _, _ = self.load([self.record('a', 'Adjustment')])
        result = reconcile(rows, self.ledger())
        self.assertFalse(result['orders'][0]['adjustment_amount_complete'])
        self.assertFalse(result['billing_ready'])

    def test_duplicate_ledger_order_rejected(self):
        ledger = self.ledger()
        ledger['orders']['duplicate'] = ledger['orders']['order'].copy()
        with self.assertRaisesRegex(ValueError, 'Duplicate tracked'):
            reconcile([], ledger)

    def test_invalid_money_rejected(self):
        for value in ['NaN', 'Infinity', '1.001', 'garbage']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load([self.record(adjustment=value)])

    def preview(self):
        rows, sources, _ = self.load([self.record(), self.record('a', 'Adjustment', 'NULL', '4.00')])
        result = reconcile(rows, self.ledger())
        result.update(sources=sources, generated_at='2026-09-23T23:00:00+00:00')
        return result

    def test_preview_validates_source_and_keeps_non_billable(self):
        self.assertFalse(validate_reconciliation(self.preview(), self.ledger())['billing_ready'])

    def test_changed_ledger_and_changed_amount_block_saved_preview(self):
        ledger = self.ledger()
        ledger['orders']['order']['status'] = 'approved'
        with self.assertRaisesRegex(ValueError, 'no longer matches'):
            validate_reconciliation(self.preview(), ledger)
        preview = self.preview()
        preview['orders'][0]['transactions'][1]['adjustment_cents'] = 1000
        with self.assertRaisesRegex(ValueError, 'differs from source'):
            validate_reconciliation(preview, self.ledger())

    def test_billable_or_altered_total_preview_rejected(self):
        preview = self.preview()
        preview['billing_ready'] = True
        with self.assertRaises(ValueError):
            validate_reconciliation(preview, self.ledger())
        preview = self.preview()
        preview['known_net_adjustment_cents'] += 1
        with self.assertRaises(ValueError):
            validate_reconciliation(preview, self.ledger())


if __name__ == '__main__':
    unittest.main()
