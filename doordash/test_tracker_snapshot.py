from copy import deepcopy
import unittest
from tracker_snapshot import parse_tables


def fixture():
    summary = [['Platform', 'Stores', 'Charges', 'Charged back ($)', 'Approved (#)', 'Denied (#)', 'Pending (#)', 'Recovered ($)', 'Denied ($)'],
      ['DoorDash', 1, 1, 10, 1, 0, 0, 10, 0],
      ['Uber Eats (acct 1)', 0, 0, 0, 0, 0, 0, 0, 0],
      ['Uber Eats (acct 2)', 1, 1, 20, 1, 0, 0, 20, 0],
      ['Uber Eats (both accts)', 1, 1, 20, 1, 0, 0, 20, 0],
      ['Grubhub (18 stores)', 18, 0, 0, 0, 0, 0, 0, 0],
      ['TOTAL (all platforms)', 20, 2, 30, 2, 0, 0, 30, 0],
      ['Generated 2026-09-21T01:33:04.422Z']]
    stores = [['Platform', 'Store', 'Charges', 'Charged back ($)', 'Approved (#)', 'Denied (#)', 'Pending (#)', 'Recovered ($)', 'Denied ($)', 'Pending ($)'],
      ['DoorDash', 'CO-1', 1, 10, 1, 0, 0, 10, 0, 0],
      ['Uber (acct 2)', 'Store 2', 1, 20, 1, 0, 0, 20, 0, 0],
      ['Grubhub', 'All stores', 0, 0, 0, 0, 0, 0, 0, 0]]
    return {'Summary': summary, 'By store': stores, 'By week': [['Sep 7 – Sep 13', '', 2, 2, 0, 0, 30]]}


class TrackerTests(unittest.TestCase):
    def test_every_platform_preserved_without_uber_subtotal_duplication(self):
        tables = fixture()
        source = deepcopy(tables)
        result = parse_tables(tables, 'DisputeTracker.xlsx', 'test-sha')
        self.assertEqual(result['reported_recovered_cents'], 3000)
        self.assertEqual(result['illustrative_ab_cut_cents'], 450)
        self.assertEqual(len(result['accounts']), 4)
        self.assertEqual(len(result['stores']), 3)
        self.assertEqual(set(result['platforms']), {'DoorDash', 'Uber Eats', 'Grubhub'})
        self.assertFalse(result['live_verified'])
        self.assertTrue(any('zero stores' in x for x in result['issues']))
        self.assertEqual(tables, source)

    def test_disagreeing_totals_stop_import(self):
        tables = fixture()
        tables['By store'][1][7] = 9
        with self.assertRaisesRegex(ValueError, 'totals disagree'):
            parse_tables(tables, 'source.xlsx', 'test')

    def test_unrecognized_additional_platform_is_preserved(self):
        tables = fixture()
        tables['Summary'].insert(-2, ['Other platform', 1, 1, 5, 1, 0, 0, 5, 0])
        tables['By store'].append(['Other platform', 'Extra store', 1, 5, 1, 0, 0, 5, 0, 0])
        tables['Summary'][-2][7] = 35
        tables['By week'][0][6] = 35
        self.assertIn('Other platform', parse_tables(tables, 'source.xlsx', 'test')['platforms'])
