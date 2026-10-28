import unittest

import gateway_sales

CONFIG = {'stores': [{'store': '3910-01', 'region': 'TX'}, {'store': '3910-02', 'region': 'TX'}]}


def report(day='10/26/2026', franchise='3910', rows=(('1', '1,000.50', '80'), ('2', '500.00', '40')), total=('1,500.50', '120')):
    head = '\n'.join(gateway_sales.COLUMNS)
    body = ''.join(f'{n}\n\t\n{s}\n\t\n{c}\n' + '\t\n0.00\n' * 9 for n, s, c in rows)
    return (f'Parameters\nStart Date\nEnd Date\nView report\nDate\n{day}\nFranchise-#\n{franchise}\nCurrency: USD\n'
            f'{head}\n\n{franchise}\n\t\n{body}Total\n\t\n{total[0]}\n\t\n{total[1]}\n' + '\t\n0.00\n' * 9
            + 'Report-Generated: 10/28/2026; Data-Source: Data Warehouse;\nPage1 of 1\n')


class RollupTests(unittest.TestCase):
    def test_store_sales_and_customer_counts_are_read_and_totals_checked(self):
        stores = gateway_sales.parse_rollup(report(), 'TX', '2026-10-26')
        self.assertEqual({n: (str(s), c) for n, (s, c) in stores.items()}, {1: ('1000.50', 80), 2: ('500.00', 40)})
        records = gateway_sales.records_for('TX', '2026-10-26', stores, CONFIG, False)
        self.assertEqual(records[0], {'store': '3910-01', 'date': '2026-10-26', 'sales': '1000.50',
                                      'transactions': 80, 'labor_pct': '0.00'})

    def test_wrong_date_franchise_or_totals_are_rejected(self):
        for text, region in ((report(day='10/25/2026'), 'TX'), (report(), 'CO'),
                             (report(total=('1,500.51', '120')), 'TX'), (report().replace('Page1 of 1', ''), 'TX')):
            with self.subTest(region=region), self.assertRaises(ValueError):
                gateway_sales.parse_rollup(text, region, '2026-10-26')

    def test_missing_store_is_unavailable_only_for_gap_recovery(self):
        stores = gateway_sales.parse_rollup(report(rows=(('1', '1,000.50', '80'),), total=('1,000.50', '80')), 'TX', '2026-10-26')
        with self.assertRaisesRegex(ValueError, 'missing'):
            gateway_sales.records_for('TX', '2026-10-26', stores, CONFIG, False)
        records = gateway_sales.records_for('TX', '2026-10-26', stores, CONFIG, True)
        self.assertEqual(records[1]['unavailable'], gateway_sales.NO_INFORMATION)


if __name__ == '__main__':
    unittest.main()
