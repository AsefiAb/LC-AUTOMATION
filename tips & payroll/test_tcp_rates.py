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



class LiveEPITests(unittest.TestCase):
    def test_live_file_lists_active_people_and_those_who_left_during_the_period(self):
        import adp_api
        from adp_export import read_template
        def w(first, last, number, status='Active', term=''):
            # Amy Bell's closed record (000009) belongs to the same ADP person as her active one.
            return {'associate_oid': 'O' + number, 'first_name': first, 'last_name': last, 'company_code': 'FND',
                    'file_number': number, 'worker_status': status, 'assignment_status': 'A' if status == 'Active' else 'T',
                    'termination_date': term, 'hire_date': '2025-01-01', 'hourly_rate': '', 'primary': True}
        workers = [w('Zed', 'Young', '000003'), w('Amy', 'Bell', '000001'),
                   w('Left', 'Recently', '000004', 'Terminated', '2026-10-22'),
                   w('Left', 'Longago', '000005', 'Terminated', '2025-03-01'),
                   {**w('Amy', 'Bell', '000009', 'Terminated', '2026-11-01'), 'associate_oid': 'O000001'}]
        raw, rows = adp_api.build_epi('FND', workers, '2026-10-20')
        self.assertEqual([r['name'] for r in rows], ['Amy Bell', 'Left Recently', 'Zed Young'])
        self.assertEqual(list(read_template(raw, 'FND', 'Wrksh001')['employees']),
                         [('FND', '1'), ('FND', '4'), ('FND', '3')])
        self.assertTrue(raw.endswith(b'!Column9,TIP,,Earnings 3 Code,Earnings 3 Amount,TIP,,80' + bytes([13, 10])))

if __name__ == '__main__':
    unittest.main()
