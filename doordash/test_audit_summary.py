import copy
import unittest
from audit_summary import summarize


def fixture():
    def transaction(id, kind, charge, adjustment):
        return {'Store ID': '123', 'Delivery UUID': 'delivery', 'DoorDash transaction ID': id,
                'Transaction type': kind, 'Timestamp local time': '2026-09-10 12:00:00', 'Payout date': '2026-09-17',
                'Error charges': f'{charge / 100:.2f}', 'Adjustments': f'{adjustment / 100:.2f}',
                'charge_cents': charge, 'adjustment_cents': adjustment}
    return {'version': 1, 'start': '2026-07-23', 'end': '2026-09-22', 'complete': True,
            'expected_orders': 1, 'transaction_count': 2, 'sources': [], 'stores': {'123': {'label': 'CO-1', 'region': 'CO'}},
            'orders': {'123:delivery': {'store_id': '123', 'delivery_uuid': 'delivery',
                'portal': {'status': 'approved', 'raw_status': 'DISPUTE_STATUS_REFUNDED'},
                'transactions': [transaction('charge', 'Error Charge', -1000, 0), transaction('credit', 'Adjustment', 0, 400)]}}}


class AuditSummaryTests(unittest.TestCase):
    def test_partial_refund_uses_recorded_credit_not_original_charge(self):
        result = summarize(fixture())
        self.assertEqual(result['totals']['charged_cents'], 1000)
        self.assertEqual(result['totals']['confirmed_credit_cents'], 400)
        self.assertEqual(result['ab_share_on_confirmed_credits_cents'], 60)
        self.assertFalse(result['billing_ready'])

    def test_denied_order_credit_is_flagged_and_not_billed(self):
        audit = fixture(); audit['orders']['123:delivery']['portal'].update(status='denied', raw_status='DISPUTE_STATUS_DENIED')
        result = summarize(audit)
        self.assertEqual(result['totals']['confirmed_credit_cents'], 0)
        self.assertEqual(result['totals']['unconfirmed_positive_adjustment_cents'], 400)
        self.assertTrue(result['issues'])

    def test_duplicate_transactions_wrong_store_and_missing_rows_fail(self):
        for change in ('duplicate', 'store', 'count'):
            with self.subTest(change=change):
                audit = fixture(); tx = audit['orders']['123:delivery']['transactions']
                if change == 'duplicate': tx.append(copy.deepcopy(tx[1]))
                if change == 'store': tx[0]['Store ID'] = '999'
                if change == 'count': audit['transaction_count'] = 3
                with self.assertRaises(ValueError): summarize(audit)

    def test_credit_outside_period_is_not_counted(self):
        audit = fixture(); audit['orders']['123:delivery']['transactions'][1]['Timestamp local time'] = '2026-09-23 12:00:00'
        audit['transaction_count'] = 1
        result = summarize(audit)
        self.assertEqual(result['totals']['confirmed_credit_cents'], 0)

    def test_missing_amount_does_not_become_verified_zero(self):
        audit = fixture(); tx = audit['orders']['123:delivery']['transactions'][1]
        tx.update(Adjustments='NULL', adjustment_cents=None)
        result = summarize(audit)
        self.assertIsNone(result['orders'][0]['confirmed_credit_cents'])
        self.assertTrue(result['issues'])


if __name__ == '__main__':
    unittest.main()
