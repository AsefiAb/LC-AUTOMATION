import json
from pathlib import Path
from datetime import datetime
import tempfile
import unittest
from email_report import build_message, deliver


class EmailTests(unittest.TestCase):
    def test_recipients_and_attachment(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / 'tracker.xlsx'
            report.write_bytes(b'fake-test-workbook')
            config = {'from': 'ab@caazllc.com', 'to': ['carter@franbiznetwork.com', 'alicia@lccollc.com'], 'subject_prefix': 'DoorDash dispute report'}
            message = build_message(config, report, datetime(2026, 9, 17))
            self.assertEqual(message['From'], 'ab@caazllc.com')
            self.assertEqual(len(list(message.iter_attachments())), 1)
            self.assertIn('unverified', message.get_body().get_content())
            config['to'].append('unexpected@example.com')
            with self.assertRaises(ValueError):
                build_message(config, report, datetime.now())

    def test_uncertain_smtp_blocks_resend(self):
        class BrokenSMTP:
            def __init__(self, *args, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def login(self, *args): pass
            def send_message(self, message): raise TimeoutError('unknown delivery')
        with tempfile.TemporaryDirectory() as folder:
            receipt = Path(folder) / 'receipt.json'
            message = {'From': 'ab@caazllc.com', 'Message-ID': '<test>'}
            with self.assertRaises(TimeoutError): deliver(message, receipt, 'test-password', BrokenSMTP)
            self.assertEqual(json.loads(receipt.read_text())['status'], 'sending')
            with self.assertRaises(FileExistsError): deliver(message, receipt, 'test-password', BrokenSMTP)


if __name__ == '__main__': unittest.main()
