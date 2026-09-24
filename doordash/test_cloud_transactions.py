import unittest
from unittest.mock import patch
from cloud_transactions import collect


HEADERS = ['Transaction ID', 'Order ID', 'Transaction type', 'Date', 'Error charges', 'Adjustments']


def page(ids, total, store='123'):
    rows = []
    for id in ids:
        cells = [{'name': 0, 'value': str(id)}, {'name': 1, 'value': str(id),
                  'action': {'payload': {'data': {'deliveryUuid': 'order-' + str(id), 'storeId': int(store)}}}},
                 {'name': 2, 'value': 'Error charge'}, {'name': 3, 'value': '9/10/2026'},
                 {'name': 4, 'value': '-$1.25'}, {'name': 5, 'value': '$0.00'}]
        rows.append(cells)
    return {'totalCount': total, 'columns': [{'name': i, 'displayString': n} for i, n in enumerate(HEADERS)], 'transactions': rows}


class Client:
    def __init__(self, pages): self.pages, self.offsets = iter(pages), []
    def get(self, url, params, timeout):
        self.offsets.append(params['offset'])
        body = next(self.pages)
        class Response:
            status_code = 200
            def json(self): return body
        return Response()


class CloudFinancialTests(unittest.TestCase):
    @patch('cloud_transactions.clock.sleep')
    def test_reads_every_page_and_preserves_negative_charges(self, sleep):
        client = Client([page(range(20), 21), page([20], 21)])
        rows = collect(client, '123', 'CO', '2026-07-23', '2026-09-22')
        self.assertEqual(client.offsets, [0, 20])
        self.assertEqual(len(rows), 21)
        self.assertEqual(rows[-1]['charge_cents'], -125)

    @patch('cloud_transactions.clock.sleep')
    def test_incomplete_repeated_and_wrong_store_pages_fail(self, sleep):
        cases = [[page([0], 2)], [page(range(20), 21), page([0], 21)], [page([0], 1, store='999')],
                 [page(range(20), 21), page([20], 22)]]
        for pages in cases:
            with self.subTest(pages=len(pages)), self.assertRaises(ValueError):
                collect(Client(pages), '123', 'CO', '2026-07-23', '2026-09-22')


if __name__ == '__main__':
    unittest.main()
