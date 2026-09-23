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

    def test_cloud_reservation_failure_prevents_smtp(self):
        from unittest.mock import Mock
        smtp = Mock()
        with tempfile.TemporaryDirectory() as folder:
            receipt = Path(folder) / 'receipt.json'
            def failed_checkpoint():
                raise RuntimeError('Cloud history unavailable')
            with self.assertRaisesRegex(RuntimeError, 'Cloud history'):
                deliver({'From': 'ab@caazllc.com', 'Message-ID': '<test>'}, receipt,
                        'test-password', smtp, checkpoint=failed_checkpoint)
            smtp.assert_not_called()
            self.assertEqual(json.loads(receipt.read_text())['status'], 'sending')

    def test_biweekly_email_has_excel_and_first_unpaid_amount(self):
        from biweekly_summary import summarize, PACIFIC
        now = datetime(2026, 9, 23, 8, tzinfo=PACIFIC)
        summary = summarize({'orders': {'x': {'store': 'CO-1', 'status': 'approved', 'approvedAmountCents': 100000}}}, {}, now)
        config = {'from': 'ab@caazllc.com', 'to': ['carter@franbiznetwork.com', 'alicia@lccollc.com']}
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / 'DoorDash.xlsx'
            report.write_bytes(b'fixture')
            message = build_message(config, report, now, summary)
            self.assertEqual(message['Subject'], "This week's dispute savings")
            self.assertIn('AB Cut: $150.00 (15%)', message.get_body().get_content())
            self.assertIn('Total still owed to AB: $150.00', message.get_body().get_content())
            self.assertEqual(list(message.iter_attachments())[0].get_filename(), 'DoorDash.xlsx')


if __name__ == '__main__': unittest.main()
