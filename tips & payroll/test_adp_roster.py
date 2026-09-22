import copy
import csv
import io
import unittest

from adp_roster import fill_region
from report_package import store_spec


class CurrentADPRosterTests(unittest.TestCase):
    def setUp(self):
        self.summary = {'employees': [
            {'geid': '1', 'first_name': 'Robin', 'last_name': 'De La Cruz', 'stores': ['3910-01', '3910-02']},
            {'geid': '2', 'first_name': 'New', 'last_name': 'Person', 'stores': ['3910-01']}],
            'source_rows': [
                {'store': '3910-01', 'geid': '1', 'regular_hours': '40.00', 'overtime_hours': '2.00', 'tips': '0.00'},
                {'store': '3910-02', 'geid': '1', 'regular_hours': '38.22', 'overtime_hours': '5.70', 'tips': '0.00'},
                {'store': '3910-01', 'geid': '2', 'regular_hours': '0.00', 'overtime_hours': '0.00', 'tips': '0.00'}]}
        self.raw = (b'!Do not change controls\r\n'
            b'!CO CODE,BATCH ID,FILE #,BATCH DESCRIPTION,Name,Rate,Reg Hours,O/T Hours,Other Earnings - TIP EARNS\r\n'
            b'!,,,,,,,,\r\n'
            b'ZKT,Batch1,0001,,Robin De La Cruz,H 20.0000,999,999,999\r\n'
            b'ZKT,Batch1,0003,,ADP Only,H 12.0000,999,999,999\r\n'
            b'!Column7,Reg Hours,,Reg Hours,,,,80\r\n'
            b'!Column8,O/T Hours,,O/T Hours,,,,80\r\n'
            b'!Column9,Other Earnings - TIP EARNS,,Earnings 3 Code,Earnings 3 Amount,T,,0\r\n')

    def test_combines_stores_preserves_control_lines_ids_rates_names_order(self):
        output, audit = fill_region(self.summary, '3910', self.raw, allow_missing_zero=True)
        self.assertTrue(audit['ready'])
        rows = [r for r in csv.reader(io.StringIO(output.decode())) if r and r[0] == 'ZKT']
        self.assertEqual(rows[0], ['ZKT','Batch1','0001','','Robin De La Cruz','H 20.0000','78.22','7.70','0.00'])
        self.assertEqual(rows[1][6:], ['0.00']*3)
        self.assertEqual(len(audit['missing']), 1)
        self.assertEqual([r for r in output.splitlines() if r.startswith(b'!')],
                         [r for r in self.raw.splitlines() if r.startswith(b'!')])

    def test_new_worker_with_hours_never_silently_omitted(self):
        self.summary['source_rows'][-1]['regular_hours'] = '9.00'
        _, audit = fill_region(self.summary, '3910', self.raw, allow_missing_zero=True)
        self.assertFalse(audit['ready'])
        self.assertTrue(any('reconcile' in x for x in audit['issues']))

    def test_adp_parenthetical_alias_is_explicit_not_fuzzy(self):
        raw = self.raw.replace(b'Robin De La Cruz', b'Roberta De La Cruz(Robin De La Cruz)')
        _, audit = fill_region(self.summary, '3910', raw, allow_missing_zero=True)
        self.assertTrue(audit['ready'])
        raw = self.raw.replace(b'Robin De La Cruz', b'Robyn De La Cruz')
        _, audit = fill_region(self.summary, '3910', raw, allow_missing_zero=True)
        self.assertFalse(audit['ready'])

    def test_duplicate_names_or_stale_override_block(self):
        raw = self.raw.replace(b'ADP Only', b'Robin De La Cruz')
        _, audit = fill_region(self.summary, '3910', raw, allow_missing_zero=True)
        self.assertFalse(audit['ready'])
        _, audit = fill_region(self.summary, '3910', self.raw, {'1': {
            'file_number':'0001', 'tcp_name':'Wrong Name', 'adp_name':'Robin De La Cruz'}}, True)
        self.assertFalse(audit['ready'])

    def test_tip_earnings_code_not_interchangeable(self):
        with self.assertRaises(ValueError):
            fill_region(self.summary, '3910', self.raw.replace(b'Amount,T,,0', b'Amount,TIP,,0'))
