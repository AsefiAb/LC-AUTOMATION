import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import build_reviewed_source
import review_sales_source
from test_sales import CONFIG


def capture_document(region, start, end, records, unavailable=()):
    return {'version': 1, 'region': region, 'start': start, 'end': end,
            'observed_at': '2026-10-12T20:00:00Z', 'system': 'Altametrics',
            'report': 'Daily Activity Report', 'records': records, 'unavailable': list(unavailable)}


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_report_is_retried_once_then_recorded_unavailable_not_zero(self):
        config = {'stores': [{'store': '3356-01', 'region': 'CO'}, {'store': '3356-02', 'region': 'CO'}]}
        metrics = {'sales': '1234.56', 'transactions': 200, 'labor_pct': '20.00'}
        reader = AsyncMock(side_effect=[metrics, None, None])
        records, unavailable = await review_sales_source.read_region_range(
            Mock(), 'CO', ['2026-10-06'], config, reader)
        self.assertEqual(records, [{'date': '2026-10-06', 'store': '3356-01', 'sales': '1234.56', 'transactions': 200}])
        self.assertEqual(unavailable, [{'date': '2026-10-06', 'store': '3356-02', 'reason': review_sales_source.NO_DATA}])
        self.assertEqual(reader.await_count, 3)

    async def test_navigation_failures_stop_instead_of_being_marked_unavailable(self):
        config = {'stores': [{'store': '3910-01', 'region': 'TX'}]}
        reader = AsyncMock(side_effect=RuntimeError('Altametrics did not retain the requested date'))
        with self.assertRaisesRegex(RuntimeError, 'did not retain'):
            await review_sales_source.read_region_range(Mock(), 'TX', ['2026-10-06'], config, reader)
        with self.assertRaisesRegex(ValueError, 'No configured stores'):
            await review_sales_source.read_region_range(Mock(), 'AZ', ['2026-10-06'], config, reader)

    def test_only_completed_consecutive_dates_are_captured(self):
        with patch.object(review_sales_source, 'datetime') as clock:
            clock.now.return_value.date.return_value = review_sales_source.date(2026, 10, 12)
            self.assertEqual(review_sales_source.dates_between('2026-10-10', '2026-10-11', 'America/Los_Angeles'),
                             ['2026-10-10', '2026-10-11'])
            for start, end in (('2026-10-11', '2026-10-12'), ('2026-10-11', '2026-10-10'), ('2026-09-01', '2026-10-05')):
                with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                    review_sales_source.dates_between(start, end, 'America/Los_Angeles')


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(CONFIG)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        for module in (build_reviewed_source, review_sales_source):
            patcher = patch.object(module, 'FOLDER', Path(self.folder.name))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(build_reviewed_source, 'datetime', wraps=build_reviewed_source.datetime)
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, region, start, end, records, unavailable=()):
        path = review_sales_source.capture_path(region, start, end)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(capture_document(region, start, end, records, unavailable)), encoding='utf-8')

    def full_region(self, region, day):
        return [{'date': day, 'store': s['store'], 'sales': '100.00', 'transactions': 10}
                for s in self.config['stores'] if s['region'] == region]

    def test_regions_with_captures_are_combined_validated_and_digested(self):
        day = '2026-10-06'
        tx = self.full_region('TX', day)
        co = self.full_region('CO', day)[1:]
        self.save('TX', day, day, tx)
        self.save('CO', day, day, co, [{'date': day, 'store': '3356-01', 'reason': review_sales_source.NO_DATA}])
        with patch.object(review_sales_source, 'datetime') as clock:
            clock.now.return_value.date.return_value = review_sales_source.date(2026, 10, 12)
            path, digest, payload = build_reviewed_source.build(day, day, 'AB', self.config)
        self.assertEqual(payload['regions'], ['CO', 'TX'])
        self.assertEqual(len(payload['records']), len(tx) + len(co))
        self.assertEqual(payload['unavailable'][0]['store'], '3356-01')
        self.assertEqual(payload['provenance']['evidence'], [f'CO-{day}_{day}.json', f'TX-{day}_{day}.json'])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.assertNotIn('AZ', payload['regions'])

    def test_incomplete_or_mismatched_captures_are_rejected_before_upload(self):
        day = '2026-10-06'
        self.save('TX', day, day, self.full_region('TX', day)[:-1])  # One store missing, not unavailable.
        with patch.object(review_sales_source, 'datetime') as clock:
            clock.now.return_value.date.return_value = review_sales_source.date(2026, 10, 12)
            with self.assertRaisesRegex(ValueError, 'every selected store/date'):
                build_reviewed_source.build(day, day, 'AB', self.config)
            with self.assertRaisesRegex(ValueError, 'No regional captures'):
                build_reviewed_source.build('2026-10-07', '2026-10-07', 'AB', self.config)
            path = review_sales_source.capture_path('AZ', '2026-10-07', '2026-10-07')
            path.write_text(json.dumps(capture_document('TX', '2026-10-07', '2026-10-07', [])), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'does not describe'):
                build_reviewed_source.build('2026-10-07', '2026-10-07', 'AB', self.config)

    def test_payload_larger_than_a_github_secret_is_rejected_before_upload(self):
        day = '2026-10-06'
        self.save('TX', day, day, self.full_region('TX', day))
        with patch.object(review_sales_source, 'datetime') as clock, \
                patch.object(build_reviewed_source, 'SECRET_LIMIT', 100):
            clock.now.return_value.date.return_value = review_sales_source.date(2026, 10, 12)
            with self.assertRaisesRegex(ValueError, 'holds at most'):
                build_reviewed_source.build(day, day, 'AB', self.config)

    def test_dispatch_sends_matching_digest_only_for_repair_modes(self):
        secrets = Mock(url='https://api.github.com/repos/AsefiAb/LC-AUTOMATION', headers={})
        with patch.object(build_reviewed_source.requests, 'post', return_value=Mock(status_code=204)) as post:
            build_reviewed_source.dispatch(secrets, 'repair-gaps', '2026-10-06', '2026-10-11', '0' * 64)
            self.assertEqual(post.call_args.kwargs['json']['inputs'],
                             {'report_date': '2026-10-06', 'end_date': '2026-10-11', 'mode': 'repair-gaps', 'source_sha256': '0' * 64})
        with self.assertRaises(ValueError):
            build_reviewed_source.dispatch(secrets, 'deliver', '2026-10-06', '2026-10-11', '0' * 64)
        with patch.object(build_reviewed_source.requests, 'post', return_value=Mock(status_code=422)):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 422'):
                build_reviewed_source.dispatch(secrets, 'repair-gaps-preview', '2026-10-06', '2026-10-11', '0' * 64)


if __name__ == '__main__':
    unittest.main()
