import unittest

from adp_roster import apply_tcp_rates


class TCPRateTests(unittest.TestCase):
    def test_matched_hourly_rows_take_tcp_rate_and_everything_else_is_unchanged(self):
        content = ('!Column1,Co Code\r\n'
                   'FND,Wrksh001,101,,Ann Bee,H 15.0000,10.00,0.00,1.00\r\n'
                   'FND,Wrksh001,102,,Cal Dee,,5.00,0.00,0.00\r\n'
                   'FND,Wrksh001,103,,Eve Fox,S 900.0000,40.00,0.00,0.00\r\n'
                   'FND,Wrksh001,104,,Gus Hay,H 14.0000,0.00,0.00,0.00\r\n').encode()
        detail = {'region': 'CO', 'employees': [
            {'file_number': '101', 'geid': '1'}, {'file_number': '102', 'geid': '2'},
            {'file_number': '103', 'geid': '3'}, {'file_number': '104', 'geid': ''}]}
        result = apply_tcp_rates(content, 'FND', detail, {'1': '16.5000', '2': '15.2500', '3': '20.0000'})
        self.assertEqual(result.decode().splitlines(), [
            '!Column1,Co Code',
            'FND,Wrksh001,101,,Ann Bee,H 16.5000,10.00,0.00,1.00',
            'FND,Wrksh001,102,,Cal Dee,H 15.2500,5.00,0.00,0.00',
            'FND,Wrksh001,103,,Eve Fox,S 900.0000,40.00,0.00,0.00',   # Salary rows keep ADP's value.
            'FND,Wrksh001,104,,Gus Hay,H 14.0000,0.00,0.00,0.00'])     # Unmatched rows unchanged.
        self.assertTrue(result.endswith(b'\r\n'))
        self.assertEqual(detail['tcp_rates'], {'rows_set': 2, 'differed_from_adp_export': 2})


if __name__ == '__main__':
    unittest.main()
