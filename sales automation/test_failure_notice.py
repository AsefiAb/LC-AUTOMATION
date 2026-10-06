import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import altametrics
import daily_report
import notify_failure


class FailureNoticeTests(unittest.TestCase):
    def test_security_failure_names_regions_only_owner_and_no_source_urls_or_attachments(self):
        problem = altametrics.AltametricsError(
            'CO: login failed: interactive verification https://private.example/token; '
            'AZ: login failed: interactive verification')
        details = daily_report.safe_failure(problem)
        message = notify_failure.failure_message('2026-10-05', {
            'failure': details, 'current_delivery': 'not_attempted'}, '12345')
        body = message.get_content()
        self.assertEqual(message['To'], 'ab@caazllc.com')
        self.assertIsNone(message['Cc'])
        self.assertEqual(list(message.iter_attachments()), [])
        self.assertIn('Colorado, Arizona', body)
        self.assertIn('did not attempt', body)
        self.assertIn('/actions/runs/12345', body)
        self.assertNotIn('private.example', body)
        self.assertNotIn('token', body)

    def test_confirmed_current_email_is_distinguished_from_old_recovery_failure(self):
        status = {'current_delivery': 'sent', 'workbook_regions_verified': ['CO', 'TX', 'AZ'],
            'recovery': {'status': 'failed', 'regions': ['CO'], 'start': '2026-09-28', 'end': '2026-10-04',
                'cause': 'Altametrics source collection did not complete.'}}
        body = notify_failure.failure_message('2026-10-05', status, '12345').get_content()
        self.assertIn('delivery was confirmed; do not resend', body)
        self.assertIn('Recovery regions: Colorado', body)
        self.assertIn('2026-09-28 through 2026-10-04', body)
        self.assertNotIn('did not attempt', body)

    def test_unknown_outcome_never_claims_nothing_was_sent_or_all_workbooks_unchanged(self):
        body = notify_failure.failure_message('2026-10-05', {
            'current_delivery': 'unconfirmed', 'workbook_updates_started': True,
            'failure': {'cause': 'https://private.example/do-this', 'regions': []}}, '12345').get_content()
        self.assertIn('delivery is unconfirmed', body)
        self.assertIn('Workbook updates began', body)
        self.assertNotIn('private.example', body)

    def test_separate_alert_receipt_deduplicates_without_smtp(self):
        ledger = Mock()
        ledger.read.return_value = {'status': 'sent'}
        with tempfile.TemporaryDirectory() as folder, patch.object(notify_failure, 'FOLDER', Path(folder)), \
                patch.dict(os.environ, {'GITHUB_REPOSITORY': 'AsefiAb/LC-AUTOMATION',
                    'GITHUB_EVENT_NAME': 'schedule', 'GITHUB_RUN_ID': '12345'}, clear=True), \
                patch.object(notify_failure, 'DeliveryLedger', return_value=ledger) as factory, \
                patch.object(daily_report.smtplib, 'SMTP_SSL') as smtp:
            (Path(folder) / 'reports').mkdir()
            (Path(folder) / 'config.json').write_text(json.dumps({'timezone': 'America/Los_Angeles'}))
            (Path(folder) / 'reports/run-status.json').write_text(json.dumps({'report_date': '2026-10-05'}))
            self.assertEqual(notify_failure.main(), 0)
            factory.assert_called_once_with('2026-10-05', namespace='sales-alert')
            smtp.assert_not_called()

    def test_complete_status_or_nonscheduled_context_cannot_send_failure_notice(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(notify_failure, 'FOLDER', Path(folder)), \
                patch.dict(os.environ, {'GITHUB_REPOSITORY': 'AsefiAb/LC-AUTOMATION',
                    'GITHUB_EVENT_NAME': 'schedule'}, clear=True), \
                patch.object(notify_failure, 'deliver') as send:
            (Path(folder) / 'reports').mkdir()
            (Path(folder) / 'reports/run-status.json').write_text(json.dumps({'complete': True}))
            self.assertEqual(notify_failure.main(), 0)
            send.assert_not_called()
            os.environ['GITHUB_EVENT_NAME'] = 'workflow_dispatch'
            with self.assertRaises(daily_report.ReportError):
                notify_failure.main()
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
