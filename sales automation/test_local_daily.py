import unittest
from datetime import date

import local_daily


class PendingDaysTests(unittest.TestCase):
    def test_nothing_to_do_when_yesterday_was_sent(self):
        self.assertEqual(local_daily.pending_days(date(2026, 10, 28), lambda day: day == '2026-10-27'), [])

    def test_missed_mornings_are_read_back_to_the_last_sent_date(self):
        sent = {'2026-10-26'}
        self.assertEqual(local_daily.pending_days(date(2026, 10, 30), sent.__contains__),
                         ['2026-10-27', '2026-10-28', '2026-10-29'])

    def test_catch_up_never_reaches_further_back_than_a_week(self):
        days = local_daily.pending_days(date(2026, 10, 30), lambda day: False)
        self.assertEqual(days, ['2026-10-23', '2026-10-24', '2026-10-25', '2026-10-26',
                                '2026-10-27', '2026-10-28', '2026-10-29'])


if __name__ == '__main__':
    unittest.main()
