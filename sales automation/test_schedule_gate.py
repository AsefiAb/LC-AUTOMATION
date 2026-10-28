import base64
import io
import json
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import schedule_gate
from test_sales import CONFIG


class ScheduleGateTests(unittest.TestCase):
    def test_config_targets_a_quarter_before_eight_pacific(self):
        self.assertEqual(schedule_gate.run_time(CONFIG), (7, 45))
        self.assertEqual(CONFIG['timezone'], 'America/Los_Angeles')

    def test_gate_opens_at_run_time_in_both_daylight_and_standard_time(self):
        cases = [('2026-10-13T14:44:00Z', False),   # 07:44 PDT
                 ('2026-10-13T14:45:00Z', True),    # 07:45 PDT
                 ('2026-10-13T15:07:00Z', True),    # 08:07 PDT
                 ('2026-12-15T15:27:00Z', False),   # 07:27 PST: the summer slots stay closed
                 ('2026-12-15T15:52:00Z', True),    # 07:52 PST
                 ('2026-12-16T06:30:00Z', True)]    # 22:30 PST the previous evening is still after 07:45
        for instant, expected in cases:
            with self.subTest(instant=instant):
                now = datetime.fromisoformat(instant.replace('Z', '+00:00'))
                self.assertEqual(schedule_gate.eligible(now, CONFIG), expected)

    def test_invalid_or_naive_inputs_are_rejected(self):
        for bad in ('', '7:45', '24:00', '07:60', 8):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                schedule_gate.run_time({**CONFIG, 'run_time': bad})
        with self.assertRaises(ValueError):
            schedule_gate.eligible(datetime(2026, 10, 13, 15), CONFIG)

    def opener(self, receipt=None, error=None):
        calls = []

        def open_url(request, timeout):
            calls.append(request.full_url)
            if error:
                raise error
            body = json.dumps({'content': base64.b64encode(json.dumps(receipt).encode()).decode()}).encode()
            response = MagicMock()
            response.__enter__.return_value = Mock(read=Mock(return_value=body))
            return response
        return open_url, calls

    def test_receipt_with_sent_email_and_complete_catch_up_closes_the_gate(self):
        token = 'test-token-not-real'
        for receipt, expected in (({'status': 'sent', 'catch_up': 'complete'}, True),
                                  ({'status': 'sent'}, False),
                                  ({'status': 'sent', 'catch_up': 'failed'}, False),
                                  ({'status': 'sent', 'catch_up': 'reviewed-source'}, True),
                                  ({'status': 'sending', 'catch_up': 'complete'}, False)):
            with self.subTest(receipt=receipt):
                open_url, calls = self.opener(receipt)
                self.assertEqual(schedule_gate.already_complete('2026-10-12', 'AsefiAb/LC-AUTOMATION', token, open_url), expected)
                self.assertIn('deliveries/2026-10-12.json?ref=sales-delivery-state', calls[0])
        open_url, calls = self.opener(error=urllib.error.HTTPError('u', 404, 'Not Found', {}, None))
        self.assertFalse(schedule_gate.already_complete('2026-10-12', 'AsefiAb/LC-AUTOMATION', token, open_url))
        open_url, calls = self.opener({'status': 'sent', 'catch_up': 'complete'})
        self.assertFalse(schedule_gate.already_complete('2026-10-12', 'other/repo', token, open_url))
        self.assertFalse(schedule_gate.already_complete('2026-10-12', 'AsefiAb/LC-AUTOMATION', '', open_url))
        self.assertFalse(schedule_gate.already_complete('bad-day', 'AsefiAb/LC-AUTOMATION', token, open_url))
        self.assertEqual(calls, [])

    def test_cli_skips_a_completed_report_date_but_never_blocks_on_ledger_errors(self):
        with patch('sys.argv', ['schedule_gate.py', '--now', '2026-10-13T15:07:00Z']), \
                patch.object(schedule_gate, 'already_complete', return_value=True) as done, \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(schedule_gate.main(), 0)
        self.assertEqual(output.getvalue().strip(), 'false')
        done.assert_called_once_with('2026-10-12')  # Yesterday in Pacific time.
        with patch('sys.argv', ['schedule_gate.py', '--now', '2026-10-13T14:00:00Z']), \
                patch.object(schedule_gate, 'already_complete') as done, redirect_stdout(io.StringIO()) as output:
            schedule_gate.main()
        self.assertEqual(output.getvalue().strip(), 'false')
        done.assert_not_called()  # Before run_time nothing is looked up.

    def test_cli_prints_true_or_false_for_an_explicit_instant(self):
        for instant, expected in (('2026-10-13T14:44:00Z', 'false'), ('2026-10-13T15:07:00+00:00', 'true')):
            with patch('sys.argv', ['schedule_gate.py', '--now', instant]), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(schedule_gate.main(), 0)
            self.assertEqual(output.getvalue().strip(), expected)


if __name__ == '__main__':
    unittest.main()
