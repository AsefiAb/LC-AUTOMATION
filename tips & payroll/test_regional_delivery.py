import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import regional_email
from send_reviewed import verify_workbook
from payroll_email_summary import FIELDS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'sales automation'))
import daily_report


class RegionalDeliveryTests(unittest.TestCase):
    def test_legacy_receipt_blocks_three_new_sends(self):
        with patch.object(daily_report, 'DeliveryLedger') as ledger:
            ledger.return_value.read.return_value = {'status': 'sent'}
            with self.assertRaisesRegex(ValueError, 'Legacy'):
                regional_email.delivery_ledgers('2026-09-21')

    def test_uncertain_region_blocks_before_any_send(self):
        with patch.object(daily_report, 'DeliveryLedger') as ledger:
            ledger.side_effect = [MagicMock(read=lambda: None), MagicMock(read=lambda: {'status': 'sent'}),
                                  MagicMock(read=lambda: {'status': 'sending'}), MagicMock(read=lambda: None)]
            with self.assertRaisesRegex(ValueError, 'uncertain'):
                regional_email.delivery_ledgers('2026-09-21')

    def test_verified_delivery_is_not_resent(self):
        ledger = MagicMock(read=lambda: {'status': 'sent'})
        from email.message import EmailMessage
        with patch.object(daily_report.smtplib, 'SMTP_SSL') as smtp:
            self.assertEqual(daily_report.deliver(EmailMessage(), ledger, '2026-09-21'), 'already sent')
            smtp.assert_not_called()

    def test_wrong_employee_amount_blocks_reviewed_package(self):
        person = {'geid': '123', 'first_name': 'Test', 'last_name': 'Person', 'job_title': 'CREW',
                  'total_hours': '22.13', 'tips': '10.00', 'regular_hours': '20.00', 'overtime_hours': '2.13'}
        values = [FIELDS, ['123', None, 'Test', 'Person', 'CREW', 22.13, 10.01, 20, 2.13],
                  ['TOTAL', None, None, None, None, 22.13, 10.01, 20, 2.13]]
        book = MagicMock(sheetnames=['3356'])
        book.active.iter_rows.return_value = values
        with patch('send_reviewed.load_workbook', return_value=book):
            with self.assertRaisesRegex(ValueError, 'Incorrect tips'):
                verify_workbook(Path('CO.xlsx'), [person], '3356')


if __name__ == '__main__':
    unittest.main()
