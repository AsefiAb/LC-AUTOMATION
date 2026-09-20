import copy
import csv
import hashlib
import io
import unittest

from adp_export import HEADER, build_exports, read_template
from gateway import REGIONS


class ADPExportTests(unittest.TestCase):
    def setUp(self):
        dates = {'start_date': '2026-09-08', 'end_date': '2026-09-21'}
        roster = [{'geid': eid} for eid in ('101', '102', '103', '201', '301')]
        def hours(eid, store, role, reg, ot):
            from decimal import Decimal
            return dict(employee_id=eid, store=store, roles=[role], name='Test employee',
                        regular_hours=reg, overtime_hours=ot, total_hours=f'{Decimal(reg)+Decimal(ot):.2f}')
        self.payroll = dict(period=dates, verified=True, issues=[], roster=roster, employees=[
            hours('101', '3356-01', 'crew leader', '20.00', '2.13'),
            hours('102', '3356-01', 'manager', '40.00', '0.00'),
            hours('102', '3356-02', 'manager', '38.22', '7.70'),
            hours('103', '3356-01', 'crew', '0.00', '0.00'),
            hours('201', '3759-01', 'crew', '10.00', '0.00'),
            hours('301', '3910-01', 'crew', '0.00', '0.00')])
        self.gateway = dict(period=dates, verified=True, issues=[], stores=[
            {'store': f'{prefix}-{i:02d}', 'total_tips': '76.63' if (prefix, i) == ('3356', 1) else '0.00'}
            for prefix, count in REGIONS.values() for i in range(1, count + 1)])
        self.templates, specs, mappings = {}, {}, []
        for region, eids in [('CO', ['101', '102', '103']), ('AZ', ['201']), ('TX', ['301'])]:
            company = 'TEST' + region
            buffer = io.StringIO(newline='')
            writer = csv.writer(buffer, lineterminator='\r\n')
            writer.writerow(['!Keep control rows', '', '', '', '', '', '', '', ''])
            writer.writerow(HEADER)
            writer.writerow(['!'] + [''] * 8)
            for eid in eids:
                writer.writerow([company, 'Batch1', '00'+eid, 'Original batch', 'Name, '+eid, 'H 16.1600', '999', '999', '999'])
                mappings.append(dict(region=region, geid=eid, company_code=company, file_number='00'+eid))
            writer.writerows([
                ['!Column7', 'Reg Hours', '', 'Reg Hours', '', '', '', '80', ''],
                ['!Column8', 'O/T Hours', '', 'O/T Hours', '', '', '', '80', ''],
                ['!Column9', 'TIP', '', 'Earnings 3 Code', 'Earnings 3 Amount', 'TIP', '', '80', '']])
            self.templates[region] = buffer.getvalue().encode('utf-8-sig')
            specs[region] = dict(company_code=company, batch_id='Batch1', sha256=hashlib.sha256(self.templates[region]).hexdigest())
        self.mapping = dict(period=dates, verified=True, issues=[], templates=specs, employees=mappings)

    def build(self):
        return build_exports(self.payroll, self.gateway, self.mapping, self.templates)

    def test_retains_roster_ids_rates_and_control_rows_and_replaces_old_values(self):
        result, audit = self.build()
        source = read_template(self.templates['CO'], 'TESTCO', 'Batch1')
        parsed = read_template(result['CO'], 'TESTCO', 'Batch1')
        self.assertEqual([raw for key, _, raw in source['parts'] if key is None],
                         [raw for key, _, raw in parsed['parts'] if key is None])
        self.assertEqual(set(parsed['employees']), set(source['employees']))
        for key, row in parsed['employees'].items():
            self.assertEqual(row[:6], source['employees'][key][:6])
        self.assertEqual(parsed['employees'][('TESTCO', '00101')][6:], ['20.00', '2.13', '76.63'])
        self.assertEqual(parsed['employees'][('TESTCO', '00102')][6:], ['78.22', '7.70', '0.00'])
        self.assertEqual(parsed['employees'][('TESTCO', '00103')][6:], ['0.00', '0.00', '0.00'])
        self.assertEqual(audit['regions']['CO']['employees'], 3)
        self.assertFalse(audit['submitted'])

    def test_missing_mapping_and_duplicate_target_stop_export(self):
        original = copy.deepcopy(self.mapping)
        self.mapping['employees'].pop()
        with self.assertRaisesRegex(ValueError, 'Every employee'):
            self.build()
        self.mapping = original
        self.mapping['employees'][1]['file_number'] = self.mapping['employees'][0]['file_number']
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.build()

    def test_unverified_inputs_and_wrong_period_stop_export(self):
        self.payroll['verified'] = False
        with self.assertRaises(ValueError): self.build()
        self.payroll['verified'] = True
        self.mapping['period'] = {'start_date': '2026-08-25', 'end_date': '2026-09-07'}
        with self.assertRaises(ValueError): self.build()

    def test_wrong_company_batch_or_changed_file_stops_export(self):
        original = copy.deepcopy(self.mapping)
        for field in ('company_code', 'batch_id', 'sha256'):
            self.mapping = copy.deepcopy(original)
            self.mapping['templates']['CO'][field] = 'wrong'
            with self.assertRaises(ValueError): self.build()

    def test_new_adp_employee_cannot_be_silently_dropped(self):
        self.templates['CO'] += b'TESTCO,Batch1,00999,,New Employee,H 10.00,,,\r\n'
        self.mapping['templates']['CO']['sha256'] = hashlib.sha256(self.templates['CO']).hexdigest()
        with self.assertRaisesRegex(ValueError, 'roster differs'): self.build()

    def test_missing_earnings_metadata_and_duplicate_employee_rejected(self):
        raw = self.templates['CO']
        with self.assertRaises(ValueError):
            read_template(raw.replace(b'Earnings 3 Amount', b'Unknown mapping'), 'TESTCO', 'Batch1')
        with self.assertRaises(ValueError):
            read_template(raw + b'TESTCO,Batch1,00101,,Duplicate,H 10,,,\r\n', 'TESTCO', 'Batch1')

    def test_roster_omission_and_hour_mismatch_stop_export(self):
        self.payroll['roster'].append({'geid': '999'})
        with self.assertRaises(ValueError): self.build()
        self.payroll['roster'].pop()
        self.payroll['employees'][0]['regular_hours'] = '0.00'
        with self.assertRaisesRegex(ValueError, 'do not add'): self.build()

    def test_windows_encoding_is_preserved(self):
        self.templates['CO'] = self.templates['CO'].decode('utf-8-sig').replace('Name, 101', 'Jos\u00e9, 101').encode('cp1252')
        self.mapping['templates']['CO']['sha256'] = hashlib.sha256(self.templates['CO']).hexdigest()
        result, _ = self.build()
        self.assertIn('Jos\u00e9, 101'.encode('cp1252'), result['CO'])


if __name__ == '__main__':
    unittest.main()
