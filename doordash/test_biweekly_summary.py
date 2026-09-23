from datetime import datetime
import unittest
from zoneinfo import ZoneInfo
from biweekly_summary import summarize, cut, schedule_due, period


class SavingsTests(unittest.TestCase):
    def now(self, value):
        return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('America/Los_Angeles'))

    def test_first_all_time_then_only_incremental_refunds_and_no_pending(self):
        rows = {'one': {'store': 'CO-1', 'status': 'approved', 'amountCents': 10000, 'approvedAmountCents': 4000},
                'two': {'store': 'TX-1', 'status': 'pending', 'amountCents': 20000}}
        first = summarize({'orders': rows}, {}, self.now('2026-09-23T08:00'))
        self.assertEqual(first['newly_verified_savings_cents'], 4000)
        self.assertEqual(first['ab_cut_cents'], 600)
        self.assertEqual(first['first_unpaid_ab_cut_cents'], 600)
        rows['two'].update(status='approved', approvedAmountCents=10000)
        second = summarize({'orders': rows}, {'2026-09-21': {'status': 'sent', 'ab_summary': first}}, self.now('2026-10-06T08:00'))
        self.assertEqual(second['newly_verified_savings_cents'], 10000)
        self.assertEqual(second['ab_cut_cents'], 1500)
        self.assertFalse(second['first_email'])
        self.assertIsNone(second['first_unpaid_ab_cut_cents'])

    def test_unknown_refund_is_an_issue_not_original_charge(self):
        summary = summarize({'orders': {'x': {'store': 'AZ-1', 'status': 'approved', 'amountCents': 99999}}}, {}, self.now('2026-09-23T08:00'))
        self.assertEqual(summary['cumulative_savings_cents'], 0)
        self.assertEqual(len(summary['issues']), 1)

    def test_penny_rounding_and_biweekly_pacific_schedule(self):
        self.assertEqual(cut(10), 2)
        for when, due in [('2026-09-22T08:00', True), ('2026-09-29T08:00', False),
                          ('2026-10-06T08:00', True), ('2026-10-06T09:00', True),
                          ('2026-11-03T08:00', True), ('2026-11-10T08:00', False)]:
            self.assertEqual(schedule_due(self.now(when)), due)
        start, end = period(self.now('2026-10-06T08:00'))
        self.assertEqual((str(start), str(end)), ('2026-09-22', '2026-10-05'))
