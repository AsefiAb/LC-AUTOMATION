import copy
import io
import json
import os
import unittest
import zipfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout

import daily_report as daily
import tcp_labor
import workbooks

CONFIG = json.loads((Path(__file__).parent / 'config.json').read_text())


class PreflightTests(unittest.TestCase):
    def test_onedrive_checked_even_when_sales_portal_is_blocked_without_writes(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(daily, 'FOLDER', Path(folder)), \
             patch.dict(os.environ, {'MS_CLIENT_ID': 'test', 'MS_REFRESH_TOKEN': 'test'}, clear=True), \
             patch('sys.argv', ['daily_report.py', '--preflight', '--date', '2026-09-16']), \
             patch.object(daily, 'checked_date', return_value='2026-09-16'), \
             patch.object(daily.altametrics, 'missing_secrets', return_value=[]), \
             patch.object(daily.altametrics, 'fetch', side_effect=daily.altametrics.AltametricsError('Interactive verification required')), \
             patch.object(daily, 'OneDrive') as drive, redirect_stdout(io.StringIO()) as output:
            drive.return_value.read.return_value = {'content': sample_workbook()}
            (Path(folder) / 'config.json').write_text(json.dumps(CONFIG), encoding='utf-8')
            self.assertEqual(daily.main(), 1)
            self.assertEqual(drive.return_value.read.call_count, 3)
            drive.return_value.write.assert_not_called()
            self.assertIn('Interactive verification required', output.getvalue())


def labor_fixture():
    day = '2026-09-16'
    jobs = {'1': {'locationName': '03356-00001'}}
    segments = [{'recordId': 1, 'timeIn': day + 'T22:00:00', 'timeOut': '2026-09-17T02:00:00',
                 'jobCode': {'jobCodeId': 1}, 'hours': {'regular': '3:30', 'overtime1': '0:30', 'overtime2': '0:00'}, 'rate': '20.00'}]
    return day, [{'segments': segments}], [{'id': 1, 'timeIn': day + 'T22:00:00'}], jobs, ['3356-01']


def sample_workbook(date_value='2026-09-16', sales_value='', formula=False, header='Store 1'):
    # Minimal synthetic XML test fixture; no real employee or financial records.
    ns = workbooks.NS['s']
    root = f'<workbook xmlns="{ns}" xmlns:r="{workbooks.NS["r"]}"><sheets><sheet name="SALES" sheetId="1" r:id="rId1"/><sheet name="Trans" sheetId="2" r:id="rId2"/></sheets><calcPr calcId="1"/></workbook>'
    rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>'
    def sheet(value):
        number = '<f>SUM(A1:A2)</f><v>12</v>' if formula else ('<v>' + value + '</v>' if value else '')
        return f'<worksheet xmlns="{ns}"><sheetData><row r="2"><c r="E2" t="inlineStr"><is><t>{header}</t></is></c></row><row r="12"><c r="A12" t="inlineStr"><is><t>{date_value}</t></is></c><c r="E12" s="3">{number}</c><c r="F12"><f>E12*2</f><v>0</v></c></row></sheetData></worksheet>'
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        z.writestr('xl/workbook.xml', root)
        z.writestr('xl/_rels/workbook.xml.rels', rels)
        z.writestr('xl/worksheets/sheet1.xml', sheet(sales_value))
        z.writestr('xl/worksheets/sheet2.xml', sheet(''))
        z.writestr('xl/drawings/example.xml', b'unchanged-drawing')
        z.writestr('xl/styles.xml', b'unchanged-styles')
    return output.getvalue()


class LaborTests(unittest.TestCase):
    def test_overnight_uses_clock_in_day_and_decimal_hours(self):
        report = tcp_labor.summarize(*labor_fixture())
        self.assertTrue(report['complete'])
        row = report['stores'][0]
        self.assertEqual(row['regular_hours'], '3.50')
        self.assertEqual(row['ot_hours'], '0.50')
        self.assertEqual(row['total_hours'], '4.00')
        self.assertEqual(row['base_wage_estimate'], '80.00')
        self.assertFalse(report['labor_cost_verified'])

    def test_missing_rate_is_not_zero_cost(self):
        args = labor_fixture()
        args[1][0]['segments'][0]['rate'] = None
        report = tcp_labor.summarize(*args)
        self.assertIsNone(report['stores'][0]['base_wage_estimate'])

    def test_open_and_zero_hour_shifts_block_complete(self):
        for condition in ('open', 'zero'):
            with self.subTest(condition=condition):
                args = labor_fixture()
                segment = args[1][0]['segments'][0]
                if condition == 'open':
                    segment['timeOut'] = None
                else:
                    segment['hours'] = {k: '0:00' for k in segment['hours']}
                self.assertFalse(tcp_labor.summarize(*args)['complete'])

    def test_missing_store_or_work_segment_blocks(self):
        args = labor_fixture()
        args[4].append('3356-02')
        self.assertFalse(tcp_labor.summarize(*args)['complete'])
        args = labor_fixture()
        args[2].append({'id': 2, 'timeIn': '2026-09-16T12:00:00'})
        self.assertFalse(tcp_labor.summarize(*args)['complete'])

    def test_rejects_nonfinite_and_invalid_minutes(self):
        for bad in ('NaN', 'Infinity', '-1', '1:99', None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                tcp_labor.duration(bad)


class WorkbookTests(unittest.TestCase):
    rows = [{'store': '3356-01', 'sales': '1234.56', 'transactions': 200}]

    def test_unpadded_store_ids_are_verified_without_accepting_wrong_store(self):
        original = sample_workbook(header='3356-1')
        self.assertEqual(workbooks.inspect_layout(original, ['3356-01'], '2026-09-16')['SALES'],
                         [('3356-01', 'E12')])
        self.assertFalse(workbooks.header_matches('3356-2', '3356-03', 3))
        self.assertFalse(workbooks.header_matches('3910-1', '3356-01', 1))

    def test_updates_exact_date_and_preserves_parts_formulas_styles(self):
        original = sample_workbook()
        updated, changes = workbooks.update(original, self.rows, '2026-09-16')
        self.assertEqual(len(changes), 2)
        with zipfile.ZipFile(io.BytesIO(updated)) as z:
            self.assertEqual(z.read('xl/drawings/example.xml'), b'unchanged-drawing')
            self.assertEqual(z.read('xl/styles.xml'), b'unchanged-styles')
            root = workbooks.xml(z.read('xl/worksheets/sheet1.xml'))
            cell = root.find('.//' + workbooks.S + 'c[@r="E12"]')
            self.assertEqual(cell.get('s'), '3')
            self.assertEqual(workbooks.cell_value(cell, []), '1234.56')
            self.assertEqual(root.find('.//' + workbooks.S + 'c[@r="F12"]/' + workbooks.S + 'f').text, 'E12*2')
        again, repeated = workbooks.update(updated, self.rows, '2026-09-16')
        self.assertEqual(repeated, [])

    def test_wrong_date_header_formula_and_existing_values_fail(self):
        for options in ({'date_value': '2026-09-15'}, {'header': 'Store 2'},
                        {'formula': True}, {'sales_value': '999'}):
            with self.subTest(options=options), self.assertRaises(workbooks.WorkbookError):
                workbooks.update(sample_workbook(**options), self.rows, '2026-09-16')

    def test_duplicate_date_does_not_append_or_guess(self):
        original = sample_workbook()
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original)) as src, zipfile.ZipFile(output, 'w') as dest:
            for info in src.infolist():
                data = src.read(info.filename)
                if info.filename.endswith('sheet1.xml'):
                    data = data.replace(b'</sheetData>', b'<row r="13"><c r="A13" t="inlineStr"><is><t>2026-09-16</t></is></c></row></sheetData>')
                dest.writestr(info, data)
        with self.assertRaises(workbooks.WorkbookError):
            workbooks.update(output.getvalue(), self.rows, '2026-09-16')


class DailyTests(unittest.TestCase):
    def fixture(self):
        config = copy.deepcopy(CONFIG)
        config['source'] = 'TCP'
        config['labor_cost_verified'] = True
        metrics = [{'store': s['store'], 'date': '2026-09-16', 'sales': '1000.00',
                    'transactions': 100, 'labor_cost': '200.00'} for s in config['stores']]
        labor = {'complete': True, 'report_date': '2026-09-16', 'issues': [],
                 'stores': [{'store': s['store'], 'regular_hours': '10.00', 'ot_hours': '1.00',
                             'total_hours': '11.00'} for s in config['stores']]}
        return config, metrics, labor

    def test_all_18_stores_and_exact_recipients(self):
        config, metrics, labor = self.fixture()
        rows = daily.validate('2026-09-16', metrics, labor, config)
        self.assertEqual(len(rows), 18)
        self.assertTrue(all(r['labor_pct'] == '20.00' for r in rows))
        message = daily.email_message('2026-09-16', rows, [], config)
        self.assertEqual(message['From'], 'ab@caazllc.com')
        self.assertEqual(message['To'], ', '.join(CONFIG['to']))
        self.assertEqual(message['Cc'], ', '.join(CONFIG['cc']))
        self.assertIn('3356-01', message.get_body(preferencelist=('plain',)).get_content())

    def test_missing_duplicate_and_wrong_date_block(self):
        for change in ('missing', 'duplicate', 'date', 'incomplete', 'fractional_transactions', 'unverified_cost'):
            with self.subTest(change=change):
                config, metrics, labor = self.fixture()
                if change == 'missing': metrics.pop()
                if change == 'duplicate': metrics.append(metrics[0])
                if change == 'date': metrics[0]['date'] = '2026-09-15'
                if change == 'incomplete': labor['complete'] = False
                if change == 'fractional_transactions': metrics[0]['transactions'] = '1.5'
                if change == 'unverified_cost': config['labor_cost_verified'] = False
                with self.assertRaises(daily.ReportError):
                    daily.validate('2026-09-16', metrics, labor, config)

    def test_zero_sales_is_not_zero_labor_percent(self):
        config, metrics, labor = self.fixture()
        metrics[0]['sales'] = '0'
        self.assertIsNone(daily.validate('2026-09-16', metrics, labor, config)[0]['labor_pct'])

    def test_altametrics_is_explicit_and_uses_its_own_credentials(self):
        self.assertEqual(CONFIG['source'], 'Altametrics')
        with patch.dict(os.environ, {}, clear=True):
            issues = daily.setup_issues(CONFIG)
            self.assertIn('Missing secret: COLORADO_USERNAME', issues)
            self.assertFalse(any('TCP_CLIENT' in i for i in issues))

    def test_pacific_yesterday_across_dst_and_utc_boundary(self):
        self.assertEqual(daily.report_date(datetime(2026, 9, 17, 2, tzinfo=timezone.utc), 'America/Los_Angeles'), '2026-09-15')
        self.assertEqual(daily.report_date(datetime(2026, 11, 1, 16, tzinfo=timezone.utc), 'America/Los_Angeles'), '2026-10-31')

    def test_ledger_sent_or_uncertain_never_sends_again(self):
        class Ledger:
            def __init__(self, status): self.status = status
            def read(self): return {'status': self.status}
        message = daily.email_message('2026-09-16', [], [], CONFIG)
        with patch('daily_report.smtplib.SMTP_SSL') as smtp:
            self.assertEqual(daily.deliver(message, Ledger('sent'), '2026-09-16'), 'already sent')
            with self.assertRaises(daily.ReportError):
                daily.deliver(message, Ledger('sending'), '2026-09-16')
            smtp.assert_not_called()

    def test_smtp_failure_leaves_sending_receipt(self):
        class Ledger:
            def __init__(self): self.receipts = []
            def read(self): return None
            def write(self, value): self.receipts.append(value)
        ledger = Ledger()
        message = daily.email_message('2026-09-16', [], [], CONFIG)
        with patch.dict(os.environ, {'CAAZ_GMAIL_APP_PASS': 'test-not-a-real-credential'}), \
             patch('daily_report.smtplib.SMTP_SSL', side_effect=OSError('not connected')):
            with self.assertRaises(daily.ReportError):
                daily.deliver(message, ledger, '2026-09-16')
        self.assertEqual([r['status'] for r in ledger.receipts], ['sending'])


if __name__ == '__main__':
    unittest.main()
