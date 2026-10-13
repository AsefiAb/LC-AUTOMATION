import copy
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
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

    def test_blocked_current_report_stops_before_a_second_catch_up_login(self):
        config, _ = self.fixture()
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'config.json').write_text(json.dumps(config))
            with patch.object(daily_report, 'FOLDER', Path(folder)), \
                    patch('sys.argv', ['daily_report.py', '--date', '2026-09-16', '--catch-up-days', '7']), \
                    patch.object(repair_workbooks, 'repair') as repair, \
                    patch.object(daily_report, 'run', side_effect=altametrics.AltametricsError(
                        'CO: login failed: Altametrics requires an interactive login or password update')):
                with self.assertRaisesRegex(altametrics.AltametricsError, 'login failed'):
                    daily_report.main()
                repair.assert_not_called()

    def test_already_delivered_date_still_recovers_older_gaps(self):
        config, _ = self.fixture()
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'config.json').write_text(json.dumps(config))
            with patch.object(daily_report, 'FOLDER', Path(folder)), \
                    patch('sys.argv', ['daily_report.py', '--date', '2026-09-16', '--catch-up-days', '7']), \
                    patch.object(repair_workbooks, 'repair', return_value={'complete': True}) as repair, \
                    patch.object(daily_report, 'run') as run:
                self.assertEqual(daily_report.main(), 0)
                run.assert_called_once_with('2026-09-16', config, False)
                self.assertEqual(repair.call_args.args[:2], ('2026-09-09', '2026-09-15'))
                self.assertTrue(repair.call_args.kwargs['gaps_only'])

    def test_non_portal_failure_still_recovers_older_gaps_before_failing(self):
        config, _ = self.fixture()
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'config.json').write_text(json.dumps(config))
            with patch.object(daily_report, 'FOLDER', Path(folder)), \
                    patch('sys.argv', ['daily_report.py', '--date', '2026-09-16', '--catch-up-days', '7']), \
                    patch.object(repair_workbooks, 'repair', return_value={'complete': True}) as repair, \
                    patch.object(daily_report, 'run', side_effect=daily_report.ReportError(
                        'A previous delivery is unresolved; inspect its receipt before retrying.')):
                with self.assertRaisesRegex(daily_report.ReportError, 'previous delivery is unresolved'):
                    daily_report.main()
                self.assertEqual(repair.call_args.args[:2], ('2026-09-09', '2026-09-15'))
            status = json.loads((Path(folder) / 'reports' / 'run-status.json').read_text())
            self.assertEqual(status['recovery'], {'status': 'complete', 'start': '2026-09-09', 'end': '2026-09-15'})

    def test_owner_notice_names_the_window_when_catch_up_was_never_attempted(self):
        import notify_failure
        config, _ = self.fixture()
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'config.json').write_text(json.dumps(config))
            with patch.object(daily_report, 'FOLDER', Path(folder)), \
                    patch('sys.argv', ['daily_report.py', '--date', '2026-09-16', '--catch-up-days', '7']), \
                    patch.object(repair_workbooks, 'repair') as repair, \
                    patch.object(daily_report, 'run', side_effect=altametrics.AltametricsError('CO: login failed: x')):
                with self.assertRaises(altametrics.AltametricsError):
                    daily_report.main()
                repair.assert_not_called()
            status = json.loads((Path(folder) / 'reports' / 'run-status.json').read_text())
            self.assertEqual(status['recovery']['status'], 'not_attempted')
            body = notify_failure.failure_message('2026-09-16', status, '1').get_content()
            self.assertIn('recovery for 2026-09-09 through 2026-09-15 was not completed', body)
            self.assertIn('did not attempt to send it', body)

    def test_catch_up_outcome_is_recorded_on_the_sent_receipt_for_later_slots(self):
        config, _ = self.fixture()
        for outcome, repair_effect in (('complete', {'complete': True}), ('failed', ValueError('Source unavailable'))):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as folder:
                (Path(folder) / 'config.json').write_text(json.dumps(config))
                ledger = Mock()
                ledger.read.return_value = {'date': '2026-09-16', 'status': 'sent'}
                with patch.object(daily_report, 'FOLDER', Path(folder)), \
                        patch('sys.argv', ['daily_report.py', '--date', '2026-09-16', '--catch-up-days', '7']), \
                        patch.object(repair_workbooks, 'repair', **({'side_effect': repair_effect} if isinstance(repair_effect, Exception) else {'return_value': repair_effect})), \
                        patch.object(daily_report, 'run'), patch.object(daily_report, 'DeliveryLedger', return_value=ledger):
                    if outcome == 'complete':
                        self.assertEqual(daily_report.main(), 0)
                    else:
                        with self.assertRaises(daily_report.ReportError):
                            daily_report.main()
                ledger.write.assert_called_once_with({'date': '2026-09-16', 'status': 'sent', 'catch_up': outcome})

    def test_receipt_marker_never_touches_unsent_or_uncertain_receipts(self):
        for receipt in (None, {'status': 'sending'}, {'status': 'sent', 'catch_up': 'complete'}):
            with self.subTest(receipt=receipt):
                ledger = Mock()
                ledger.read.return_value = receipt
                with patch.object(daily_report, 'DeliveryLedger', return_value=ledger):
                    daily_report.record_catch_up('2026-09-16', 'complete')
                ledger.write.assert_not_called()
        with patch.object(daily_report, 'DeliveryLedger', side_effect=daily_report.ReportError('no ledger')):
            daily_report.record_catch_up('2026-09-16', 'complete')  # Never raises.

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


class MissingStoreDayTests(unittest.IsolatedAsyncioTestCase):
    def page(self, no_information):
        page = Mock()
        body = Mock(inner_text=AsyncMock(return_value=altametrics.NO_INFORMATION if no_information else 'Daily Activity Report'))
        chain = Mock()
        chain.locator.return_value.locator.return_value.input_value = AsyncMock(return_value='03356-00003')
        page.locator.side_effect = lambda selector: {'body': body, '#FILTER_STORE_GO': chain}[selector]
        return page

    async def test_no_information_store_day_is_recorded_unavailable_only_when_allowed(self):
        config = {'stores': [{'store': '3356-03', 'region': 'CO'}]}
        empty = [[['Time', 'Product Net', 'Trans', '', '', '', '', 'Punched Labor %'], ['Actual Total', '', '', '', '', '', '', '']]]
        with patch.object(altametrics, 'select_store', AsyncMock()) as select, \
                patch.object(altametrics, 'select_date', AsyncMock()), \
                patch.object(altametrics, 'report_tables', AsyncMock(return_value=empty)):
            rows = await altametrics.read_region_report(self.page(True), 'CO', '2026-10-05', config, allow_missing=True)
            self.assertEqual(rows, [{'store': '3356-03', 'date': '2026-10-05', 'unavailable': altametrics.NO_INFORMATION}])
            self.assertEqual(select.await_count, 2)  # One re-read before recording.
            with self.assertRaises(ValueError):
                await altametrics.read_region_report(self.page(True), 'CO', '2026-10-05', config)
            with self.assertRaises(ValueError):  # Empty table without the dashboard's statement is an error.
                await altametrics.read_region_report(self.page(False), 'CO', '2026-10-05', config, allow_missing=True)

    def test_partial_validation_keeps_full_coverage_and_strict_daily_validation(self):
        config = {'stores': [{'store': '3356-01', 'region': 'CO'}, {'store': '3356-03', 'region': 'CO'}]}
        good = {'store': '3356-01', 'date': '2026-10-05', 'sales': '100.00', 'transactions': 10, 'labor_pct': '0'}
        gone = {'store': '3356-03', 'date': '2026-10-05', 'unavailable': altametrics.NO_INFORMATION}
        rows, unavailable = altametrics.validate_partial('2026-10-05', [good, gone], config)
        self.assertEqual([r['store'] for r in rows], ['3356-01'])
        self.assertEqual(unavailable, [{'date': '2026-10-05', 'store': '3356-03', 'reason': altametrics.NO_INFORMATION}])
        with self.assertRaises(ValueError):
            altametrics.validate_partial('2026-10-05', [good], config)  # Missing store is not implicitly unavailable.
        with self.assertRaises(ValueError):
            altametrics.validate_partial('2026-10-05', [good, {**gone, 'unavailable': 'closed'}], config)
        with self.assertRaisesRegex(ValueError, 'every store is required'):
            altametrics.validate('2026-10-05', [good, gone], config)


class ReviewedSourceTests(unittest.TestCase):
    def fixture(self):
        config = copy.deepcopy(CONFIG)
        config['stores'] = [config['stores'][0], next(s for s in config['stores'] if s['store'] == '3759-01')]
        payload = {'schema_version': 1, 'start': '2026-09-16', 'end': '2026-09-16',
            'regions': ['AZ'], 'provenance': {'system': 'Altametrics',
            'report': 'Daily Activity Report', 'sales_metric': 'Product Net',
            'transactions_metric': 'Actual Total transactions', 'method': 'authenticated_browser_review',
            'observed_at': '2026-09-17T15:00:00Z', 'reviewed_by': 'test reviewer',
            'evidence': ['AZ-2026-09-16-ui.json']},
            'records': [{'date': '2026-09-16', 'store': '3759-01', 'sales': '1234.56', 'transactions': 200}]}
        return config, payload

    def save(self, folder, payload):
        path = Path(folder) / 'reviewed.json'
        content = json.dumps(payload).encode('utf-8')
        path.write_bytes(content)
        return path, hashlib.sha256(content).hexdigest()

    def test_region_subset_retains_coverage_and_provenance_without_inventing_labor(self):
        config, payload = self.fixture()
        with tempfile.TemporaryDirectory() as folder:
            path, digest = self.save(folder, payload)
            selected, rows, reviewed = repair_workbooks.reviewed_source(
                path, digest, payload['start'], payload['end'], config)
            self.assertEqual([b['region'] for b in selected['workbooks']], ['AZ'])
            self.assertEqual([s['store'] for s in selected['stores']], ['3759-01'])
            self.assertEqual(rows['2026-09-16'][0]['sales'], '1234.56')
            self.assertNotIn('labor_pct', rows['2026-09-16'][0])
            self.assertEqual(reviewed['provenance'], payload['provenance'])
            self.assertEqual(len(config['workbooks']), 3)  # Caller remains unchanged.

    def test_rejects_bad_provenance_values_coverage_and_scope_before_onedrive(self):
        cases = ['missing_store', 'duplicate', 'wrong_date', 'unknown_store', 'unknown_region',
                 'bad_metric', 'no_evidence', 'session_url', 'missing_reviewer', 'nan',
                 'negative', 'fractional_transaction', 'extra_precision', 'wrong_period', 'duplicate_region']
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                config, payload = self.fixture()
                if case == 'missing_store': payload['records'] = []
                if case == 'duplicate': payload['records'] *= 2
                if case == 'wrong_date': payload['records'][0]['date'] = '2026-09-15'
                if case == 'unknown_store': payload['records'][0]['store'] = '3759-99'
                if case == 'unknown_region': payload['regions'] = ['BAD']
                if case == 'duplicate_region': payload['regions'] *= 2
                if case == 'bad_metric': payload['provenance']['sales_metric'] = 'Gross sales'
                if case == 'no_evidence': payload['provenance']['evidence'] = []
                if case == 'session_url': payload['provenance']['evidence'] = ['https://example.test/session']
                if case == 'missing_reviewer': payload['provenance']['reviewed_by'] = ''
                if case == 'nan': payload['records'][0]['sales'] = 'NaN'
                if case == 'negative': payload['records'][0]['sales'] = '-1'
                if case == 'fractional_transaction': payload['records'][0]['transactions'] = 1.5
                if case == 'extra_precision': payload['records'][0]['sales'] = '1234.567'
                if case == 'wrong_period': payload['end'] = '2026-09-17'
                path, digest = self.save(folder, payload)
                with patch.object(repair_workbooks, 'OneDrive') as drive:
                    with self.assertRaises(ValueError):
                        repair_workbooks.repair('2026-09-16', '2026-09-16', config,
                            source_records=path, source_sha256=digest)
                    drive.assert_not_called()

    def test_digest_binds_dispatch_to_exact_secret_bytes(self):
        config, payload = self.fixture()
        with tempfile.TemporaryDirectory() as folder:
            path, digest = self.save(folder, payload)
            with self.assertRaisesRegex(ValueError, 'digest does not match'):
                repair_workbooks.reviewed_source(path, '0'*64, payload['start'], payload['end'], config)

    def test_manual_numbers_cannot_silently_strip_bad_currency_or_grouping(self):
        bad_sales = ['1,23.45', '$$123', '$123', '1,234.56', '123 ', '+123', '01.25',
                     '1e3', True, False, 'NaN']
        bad_transactions = ['$123', '1,23', '123.0', 123.0, '01', True, False]
        for field, values in [('sales', bad_sales), ('transactions', bad_transactions)]:
            for value in values:
                with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as folder:
                    config, payload = self.fixture()
                    payload['records'][0][field] = value
                    path, digest = self.save(folder, payload)
                    with self.assertRaisesRegex(ValueError, 'canonical'):
                        repair_workbooks.reviewed_source(path, digest, payload['start'], payload['end'], config)

    def test_observation_must_follow_the_complete_requested_period_in_local_time(self):
        for observed in ('2026-09-15T15:00:00Z', '2026-09-16T22:00:00Z', '2026-09-17T02:00:00Z'):
            with self.subTest(observed=observed), tempfile.TemporaryDirectory() as folder:
                config, payload = self.fixture()
                payload['provenance']['observed_at'] = observed
                path, digest = self.save(folder, payload)
                with self.assertRaisesRegex(ValueError, 'after the requested report period'):
                    repair_workbooks.reviewed_source(path, digest, payload['start'], payload['end'], config)

    def test_reviewed_preview_uses_only_selected_workbook_and_never_logs_in_to_altametrics(self):
        config, payload = self.fixture()
        with tempfile.TemporaryDirectory() as folder, patch.object(repair_workbooks, 'FOLDER', Path(folder)), \
                patch.object(repair_workbooks, 'OneDrive') as drive, \
                patch.object(repair_workbooks.altametrics, 'fetch_many') as fetch:
            path, digest = self.save(folder, payload)
            drive.return_value.read.return_value = {'content': sample_workbook(header='3759-01')}
            result = repair_workbooks.repair('2026-09-16', '2026-09-16', config, preview=True,
                source_records=path, source_sha256=digest)
            drive.return_value.read.assert_called_once_with(CONFIG['workbooks'][2]['url'])
            drive.return_value.write.assert_not_called()
            fetch.assert_not_called()
            self.assertEqual(result['selected_regions'], ['AZ'])
            self.assertEqual(result['source_sha256'], digest)
            self.assertEqual(result['emails_sent'], 0)

    def test_explicit_unavailable_middle_store_preserves_column_and_reports_partial_completion(self):
        config, payload = self.fixture()
        config['stores'] = CONFIG['stores'][:3]
        config['workbooks'] = CONFIG['workbooks'][:1]
        payload['regions'] = ['CO']
        payload['records'] = [
            {'date': '2026-09-16', 'store': '3356-01', 'sales': '1234.56', 'transactions': 200},
            {'date': '2026-09-16', 'store': '3356-03', 'sales': '5678.90', 'transactions': 300}]
        payload['unavailable'] = [{'date': '2026-09-16', 'store': '3356-02',
                                   'reason': 'No Information present for this Selection'}]
        ns = workbooks.NS['s']
        sheet = (f'<worksheet xmlns="{ns}"><sheetData><row r="2">'
            + ''.join(f'<c r="{column}2" t="inlineStr"><is><t>{store}</t></is></c>'
                      for column, store in zip('EFG', ['3356-01', '3356-02', '3356-03']))
            + '</row><row r="12"><c r="A12" t="inlineStr"><is><t>2026-09-16</t></is></c>'
            + '<c r="E12" s="3"/><c r="F12" s="3"><v>888</v></c><c r="G12" s="3"/>'
            + '<c r="H12"><f>SUM(E12:G12)</f><v>0</v></c></row></sheetData></worksheet>')
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(sample_workbook())) as source, zipfile.ZipFile(output, 'w') as dest:
            for item in source.infolist():
                data = sheet.encode() if item.filename in ('xl/worksheets/sheet1.xml', 'xl/worksheets/sheet2.xml') else source.read(item.filename)
                dest.writestr(item, data)
        original = output.getvalue()
        updated, _ = workbooks.update(original, payload['records'], '2026-09-16',
            sales_whole_dollars=True, replace_conflicts=True,
            store_layout=['3356-01', '3356-02', '3356-03'])
        with tempfile.TemporaryDirectory() as folder, patch.object(repair_workbooks, 'FOLDER', Path(folder)), \
                patch.object(repair_workbooks, 'OneDrive') as drive, \
                patch.object(repair_workbooks.altametrics, 'fetch_many') as fetch:
            path, digest = self.save(folder, payload)
            drive.return_value.read.side_effect = [{'content': original}, {'content': updated}]
            result = repair_workbooks.repair('2026-09-16', '2026-09-16', config,
                source_records=path, source_sha256=digest)
            self.assertFalse(result['complete'])
            self.assertTrue(result['available_records_verified'])
            self.assertEqual(len(result['workbooks'][0]['changes']), 4)
            self.assertEqual({c['cell'] for c in result['unverified_cells']}, {'F12'})
            self.assertEqual({c['value'] for c in result['unverified_cells']}, {'888'})
            fetch.assert_not_called()
            sent_content = drive.return_value.write.call_args.args[1]
            cells = workbooks.input_cells(sent_content, ['3356-01', '3356-02', '3356-03'], '2026-09-16')
            sales = {c['cell']: c['value'] for c in cells if c['sheet'] == 'SALES'}
            self.assertEqual(sales, {'E12': '1235', 'F12': '888', 'G12': '5679'})
            with zipfile.ZipFile(io.BytesIO(sent_content)) as book:
                root = workbooks.xml(book.read('xl/worksheets/sheet1.xml'))
                self.assertEqual(root.find('.//' + workbooks.S + 'c[@r="H12"]/' + workbooks.S + 'f').text, 'SUM(E12:G12)')

    def test_fresh_gaps_recovery_preserves_a_no_information_store_day_and_reports_it(self):
        config, _ = self.fixture()
        config['stores'] = CONFIG['stores'][:3]
        config['workbooks'] = CONFIG['workbooks'][:1]
        day = '2026-09-16'
        records = [{'store': '3356-01', 'date': day, 'sales': '1234.56', 'transactions': 200, 'labor_pct': '0'},
                   {'store': '3356-02', 'date': day, 'unavailable': altametrics.NO_INFORMATION},
                   {'store': '3356-03', 'date': day, 'sales': '5678.90', 'transactions': 300, 'labor_pct': '0'}]
        ns = workbooks.NS['s']
        sheet = (f'<worksheet xmlns="{ns}"><sheetData><row r="2">'
            + ''.join(f'<c r="{column}2" t="inlineStr"><is><t>{store}</t></is></c>'
                      for column, store in zip('EFG', ['3356-01', '3356-02', '3356-03']))
            + f'</row><row r="12"><c r="A12" t="inlineStr"><is><t>{day}</t></is></c>'
            + '<c r="E12" s="3"/><c r="F12" s="3"/><c r="G12" s="3"/></row></sheetData></worksheet>')
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(sample_workbook())) as source, zipfile.ZipFile(output, 'w') as dest:
            for item in source.infolist():
                data = sheet.encode() if item.filename in ('xl/worksheets/sheet1.xml', 'xl/worksheets/sheet2.xml') else source.read(item.filename)
                dest.writestr(item, data)
        original = output.getvalue()
        updated, _ = workbooks.update(original, [r for r in records if 'sales' in r], day,
            sales_whole_dollars=True, only_blank=True, store_layout=['3356-01', '3356-02', '3356-03'])
        with tempfile.TemporaryDirectory() as folder, patch.object(repair_workbooks, 'FOLDER', Path(folder)), \
                patch.object(repair_workbooks, 'OneDrive') as drive, \
                patch.object(repair_workbooks.altametrics, 'fetch_many', return_value=records) as fetch:
            drive.return_value.read.side_effect = [{'content': original}, {'content': updated}]
            result = repair_workbooks.repair(day, day, config, gaps_only=True)
            self.assertTrue(fetch.call_args.kwargs['allow_missing'])
            self.assertFalse(result['complete'])
            self.assertTrue(result['workbooks'][0]['verified'])
            self.assertEqual(result['unavailable'], [{'date': day, 'store': '3356-02', 'reason': altametrics.NO_INFORMATION}])
            self.assertEqual({(c['sheet'], c['cell']) for c in result['unverified_cells']}, {('SALES', 'F12'), ('TRANS', 'F12')})
            sent = drive.return_value.write.call_args.args[1]
            sales = {c['cell']: c['value'] for c in workbooks.input_cells(sent, ['3356-01', '3356-02', '3356-03'], day) if c['sheet'] == 'SALES'}
            self.assertEqual(sales, {'E12': '1235', 'F12': '', 'G12': '5679'})
            # Value corrections keep requiring every store.
            fetch.reset_mock()
            drive.return_value.read.side_effect = [{'content': original}]
            with self.assertRaisesRegex(ValueError, 'every store is required'):
                repair_workbooks.repair(day, day, config)
            self.assertFalse(fetch.call_args.kwargs['allow_missing'])

    def test_unavailable_cannot_overlap_records_or_hide_unknown_store(self):
        for store in ('3759-01', '3759-99'):
            with self.subTest(store=store), tempfile.TemporaryDirectory() as folder:
                config, payload = self.fixture()
                payload['unavailable'] = [{'date': '2026-09-16', 'store': store, 'reason': 'No information'}]
                path, digest = self.save(folder, payload)
                with self.assertRaisesRegex(ValueError, 'duplicates a record|unknown store'):
                    repair_workbooks.reviewed_source(path, digest, payload['start'], payload['end'], config)

    def test_partial_repair_cli_exits_incomplete_even_after_verified_available_writes(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(repair_workbooks, 'FOLDER', Path(folder)), \
                patch('sys.argv', ['repair_workbooks.py', '--start', '2026-09-16', '--end', '2026-09-16']), \
                patch.object(repair_workbooks, 'repair', return_value={'complete': False, 'available_records_verified': True}):
            (Path(folder) / 'config.json').write_text(json.dumps(CONFIG))
            self.assertEqual(repair_workbooks.main(), 2)


if __name__ == '__main__':
    unittest.main()
