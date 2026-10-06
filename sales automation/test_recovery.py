import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import altametrics
import onedrive
import daily_report
import repair_workbooks
import workbooks
from test_sales import CONFIG, sample_workbook


def response(status, *, data=None, content=b'', headers=None):
    return Mock(status_code=status, ok=status < 400, content=content,
                headers=headers or {}, json=Mock(return_value=data))


class DownloadRecoveryTests(unittest.TestCase):
    def drive(self):
        drive = onedrive.OneDrive.__new__(onedrive.OneDrive)
        drive.token = 'test-token'
        drive.request = Mock(side_effect=[
            response(200, data={'id': 'book', 'parentReference': {'driveId': 'drive'}}),
            response(200, data={'eTag': 'original-version', 'size': 10,
                               '@microsoft.graph.downloadUrl': 'https://test.1drv.com/expired'}),
            response(302, headers={'Location': 'https://test.1drv.com/renewed'})])
        return drive

    def test_expired_signed_download_renews_via_authenticated_graph(self):
        drive = self.drive()
        with patch.object(onedrive, 'read_request', side_effect=[response(401), response(200, content=b'xlsx')]) as read:
            result = drive.read('https://1drv.ms/x/example')
        self.assertEqual(result['content'], b'xlsx')
        self.assertEqual(result['etag'], 'original-version')
        self.assertEqual(drive.request.call_args.args, ('GET', '/drives/drive/items/book/content'))
        self.assertEqual(read.call_count, 2)
        self.assertNotIn('headers', read.call_args.kwargs)  # No bearer to download host.

    def test_persistent_authorization_error_does_not_loop(self):
        drive = self.drive()
        with patch.object(onedrive, 'read_request', return_value=response(401)) as read:
            with self.assertRaisesRegex(onedrive.DriveError, 'HTTP 401'):
                drive.read('https://1drv.ms/x/example')
        self.assertEqual(read.call_count, 2)

    def test_read_retries_throttling_but_put_does_not_retry(self):
        with patch.object(onedrive.requests, 'request', side_effect=[response(503), response(200)]) as request, \
                patch.object(onedrive.time, 'sleep'):
            self.assertEqual(onedrive.read_request('GET', 'https://example.test').status_code, 200)
            self.assertEqual(request.call_count, 2)
        drive = onedrive.OneDrive.__new__(onedrive.OneDrive)
        drive.token = 'test'
        with patch.object(onedrive.requests, 'request', return_value=response(412)) as request:
            with self.assertRaisesRegex(onedrive.DriveError, 'HTTP 412'):
                drive.write({'path': '/test', 'etag': 'old'}, b'xlsx')
            request.assert_called_once()


class MultiDateSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_date_runs_verified_store_navigation_and_keeps_its_own_snapshot(self):
        dates = ['2026-09-30', '2026-10-01']
        records = [[{'store': '3356-01', 'date': day, 'sales': str(100 + i),
                     'transactions': 10, 'labor_pct': '0'}] for i, day in enumerate(dates)]
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(altametrics, '__file__', str(Path(folder) / 'altametrics.py')), \
                patch.object(altametrics, 'read_region_report', AsyncMock(side_effect=records)) as read:
            result = await altametrics.read_region_days(Mock(), 'CO', dates, {})
            self.assertEqual([call.args[2] for call in read.await_args_list], dates)
            self.assertEqual(result, records[0] + records[1])
            for day, expected in zip(dates, records):
                saved = Path(folder) / 'reports/source-snapshots' / day / 'CO.json'
                self.assertEqual(json.loads(saved.read_text()), expected)

    async def test_later_failure_keeps_completed_source_but_does_not_return_partial_range(self):
        first = [{'store': '3356-01', 'date': '2026-09-30', 'sales': '100',
                  'transactions': 10, 'labor_pct': '0'}]
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(altametrics, '__file__', str(Path(folder) / 'altametrics.py')), \
                patch.object(altametrics, 'read_region_report',
                    AsyncMock(side_effect=[first, RuntimeError('Report date did not match')])):
            with self.assertRaisesRegex(RuntimeError, 'date did not match'):
                await altametrics.read_region_days(Mock(), 'CO', ['2026-09-30', '2026-10-01'], {})
            self.assertTrue((Path(folder) / 'reports/source-snapshots/2026-09-30/CO.json').exists())
            self.assertFalse((Path(folder) / 'reports/source-snapshots/2026-10-01/CO.json').exists())


class WorkbookRecoveryTests(unittest.TestCase):
    def fixture(self):
        config = copy.deepcopy(CONFIG)
        config['stores'] = config['stores'][:1]
        config['workbooks'] = config['workbooks'][:1]
        records = [{'store': '3356-01', 'date': '2026-09-16', 'sales': '1234.56',
                    'transactions': 200, 'labor_pct': '0'}]
        return config, records

    def test_correction_preserves_formula_and_style_and_records_old_value(self):
        rows = [{'store': '3356-01', 'sales': '1234.56', 'transactions': 200}]
        original = sample_workbook(sales_value='999')
        corrected, changes = workbooks.update(original, rows, '2026-09-16', replace_conflicts=True)
        self.assertEqual(changes[0]['old_value'], '999')
        self.assertEqual(changes[0]['value'], '1234.56')
        cells = workbooks.input_cells(corrected, ['3356-01'], '2026-09-16')
        self.assertEqual(cells[0]['value'], '1234.56')
        with self.assertRaises(workbooks.WorkbookError):
            workbooks.update(sample_workbook(formula=True), rows, '2026-09-16', replace_conflicts=True)

    def test_blank_recovery_does_not_overwrite_prior_nonblank_input(self):
        rows = [{'store': '3356-01', 'sales': '1234.56', 'transactions': 200}]
        recovered, changes = workbooks.update(sample_workbook(sales_value='999'), rows,
                                              '2026-09-16', only_blank=True)
        self.assertEqual([c['sheet'] for c in changes], ['TRANS'])
        self.assertEqual(workbooks.input_cells(recovered, ['3356-01'], '2026-09-16')[0]['value'], '999')

    def test_preview_writes_private_audit_and_backup_without_upload(self):
        config, records = self.fixture()
        with tempfile.TemporaryDirectory() as folder, patch.object(repair_workbooks, 'FOLDER', Path(folder)), \
                patch.object(repair_workbooks, 'OneDrive') as drive, \
                patch.object(repair_workbooks.altametrics, 'fetch_many', return_value=records):
            drive.return_value.read.return_value = {'content': sample_workbook(sales_value='999')}
            result = repair_workbooks.repair('2026-09-16', '2026-09-16', config, preview=True)
            drive.return_value.write.assert_not_called()
            self.assertTrue(result['complete'])
            self.assertEqual(result['emails_sent'], 0)
            saved = Path(folder) / 'reports/workbook-repair-2026-09-16_2026-09-16'
            self.assertTrue((saved / 'CO.before.xlsx').exists())
            self.assertTrue((saved / 'CO.after.xlsx').exists())
            self.assertEqual(json.loads((saved / 'repair-audit.json').read_text())['workbooks'][0]['changes'][0]['old_value'], '999')

    def test_missing_source_store_prevents_all_uploads(self):
        config, records = self.fixture()
        with tempfile.TemporaryDirectory() as folder, patch.object(repair_workbooks, 'FOLDER', Path(folder)), \
                patch.object(repair_workbooks, 'OneDrive') as drive, \
                patch.object(repair_workbooks.altametrics, 'fetch_many', return_value=[]):
            drive.return_value.read.return_value = {'content': sample_workbook()}
            with self.assertRaisesRegex(ValueError, 'every configured store'):
                repair_workbooks.repair('2026-09-16', '2026-09-16', config)
            drive.return_value.write.assert_not_called()

    def test_live_repair_requires_readback_to_confirm_values(self):
        config, records = self.fixture()
        with tempfile.TemporaryDirectory() as folder, patch.object(repair_workbooks, 'FOLDER', Path(folder)), \
                patch.object(repair_workbooks, 'OneDrive') as drive, \
                patch.object(repair_workbooks.altametrics, 'fetch_many', return_value=records):
            # Provider accepting the PUT is not evidence that the right cells persisted.
            drive.return_value.read.return_value = {'content': sample_workbook(sales_value='999')}
            with self.assertRaisesRegex(workbooks.WorkbookError, 'did not retain'):
                repair_workbooks.repair('2026-09-16', '2026-09-16', config)
            drive.return_value.write.assert_called_once()

    def test_failed_old_gap_recovery_does_not_prevent_current_delivery_or_hide_failure(self):
        config, _ = self.fixture()
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'config.json').write_text(json.dumps(config))
            with patch.object(daily_report, 'FOLDER', Path(folder)), \
                    patch('sys.argv', ['daily_report.py', '--date', '2026-09-16', '--catch-up-days', '7']), \
                    patch.object(repair_workbooks, 'repair', side_effect=ValueError('Source unavailable')) as repair, \
                    patch.object(daily_report, 'run') as run:
                with self.assertRaisesRegex(daily_report.ReportError, 'Current report completed'):
                    daily_report.main()
                self.assertEqual(repair.call_args.args[:2], ('2026-09-09', '2026-09-15'))
                run.assert_called_once_with('2026-09-16', config, False)

    def test_successful_repair_reads_back_each_changed_cell(self):
        config, records = self.fixture()
        original = sample_workbook(sales_value='999')
        updated, _ = workbooks.update(original,
            [{'store': '3356-01', 'sales': '1234.56', 'transactions': 200}],
            '2026-09-16', sales_whole_dollars=True, replace_conflicts=True)
        with tempfile.TemporaryDirectory() as folder, patch.object(repair_workbooks, 'FOLDER', Path(folder)), \
                patch.object(repair_workbooks, 'OneDrive') as drive, \
                patch.object(repair_workbooks.altametrics, 'fetch_many', return_value=records):
            drive.return_value.read.side_effect = [{'content': original}, {'content': updated}]
            result = repair_workbooks.repair('2026-09-16', '2026-09-16', config)
            self.assertTrue(result['complete'])
            self.assertTrue(result['workbooks'][0]['verified'])


if __name__ == '__main__':
    unittest.main()
