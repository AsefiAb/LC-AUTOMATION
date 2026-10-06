import base64
import csv
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adp_roster import fill_region, validate_import
from review_adp import prepare_candidate, issue_notes
from review_package import prepare_review, combine
import test_adp_roster
import test_review_package


class ReviewEPITests(unittest.TestCase):
    def setUp(self):
        fixture = test_adp_roster.CurrentADPRosterTests()
        fixture.setUp()
        self.raw = fixture.raw
        self.people = [dict(geid='1', first_name='Robin', last_name='De La Cruz',
                           regular_hours='78.22', overtime_hours='7.70', tips=None),
                       dict(geid='2', first_name='New', last_name='Person',
                           regular_hours='9.00', overtime_hours='0.00', tips='4.25')]
        self.dates = {'start_date': '2026-09-22', 'end_date': '2026-10-05'}

    def test_unknown_amounts_blank_known_hours_preserved_and_missing_people_accounted_for(self):
        raw, audit = fill_region(None, '3910', self.raw, review_people=self.people)
        rows = [r for r in csv.reader(io.StringIO(raw.decode())) if r and r[0] == 'ZKT']
        self.assertEqual(rows[0][2:], ['0001', '', 'Robin De La Cruz', 'H 20.0000', '78.22', '7.70', ''])
        self.assertEqual(rows[1][6:], ['', '', ''])
        self.assertEqual([r for r in raw.splitlines() if r.startswith(b'!')],
                         [r for r in self.raw.splitlines() if r.startswith(b'!')])
        self.assertFalse(audit['ready'])
        self.assertEqual(audit['missing'][0]['regular_hours'], '9.00')
        self.assertEqual(audit['totals'], dict(regular_hours='78.22', overtime_hours='7.70', tips='0.00'))
        self.assertTrue(any('New Person' in n and '4.25' in n and 'NOT IN CSV' in n for n in issue_notes(audit)))
        self.assertEqual(validate_import('3910', 'EPIZKT00.CSV', raw, allow_blank=True)['employee_rows'], 2)
        with self.assertRaises(ValueError):
            validate_import('3910', 'EPIZKT00.CSV', raw)

    def test_unknown_pay_does_not_make_missing_employee_a_zero_hour_exclusion(self):
        self.people[1].update(regular_hours=None, tips=None)
        _, audit = fill_region(None, '3910', self.raw, allow_missing_zero=True, review_people=self.people)
        self.assertTrue(any('GEID 2: ADP identity missing' in x for x in audit['issues']))

    def test_stale_roster_delivers_candidate_without_claiming_ready(self):
        env = {'ADP_TX_TEMPLATE': base64.b64encode(self.raw).decode(),
               'ADP_ROSTER_SETTINGS': json.dumps({'period': {'start_date': '2026-09-08', 'end_date': '2026-09-21'}})}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, env, clear=True):
            name, audit = prepare_candidate('3910', self.people, self.dates, Path(folder))
            self.assertEqual(name, 'EPIZKT00.CSV')
            self.assertTrue((Path(folder) / name).exists())
            self.assertFalse(audit['ready'])
            self.assertTrue(any('last supplied ADP roster' in i for i in audit['issues']))

    def test_all_three_emails_include_native_csv_even_with_source_issues(self):
        fixture = test_review_package.PayrollReviewTests()
        fixture.setUp()
        env = {'GITHUB_ACTIONS': 'true', 'ADP_ROSTER_SETTINGS': '{}'}
        for code, company in [('TX', 'ZKT'), ('CO', 'FND'), ('AZ', 'FNC')]:
            raw = self.raw.replace(b'ZKT', company.encode()).replace(b'Robin De La Cruz', b'Test Worker')
            if code != 'TX':
                raw = raw.replace(b'Other Earnings - TIP EARNS', b'TIP').replace(b'Amount,T,,0', b'Amount,TIP,,80')
            env['ADP_' + code + '_TEMPLATE'] = base64.b64encode(raw).decode()
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, env, clear=True):
            messages = prepare_review(fixture.dates, Path(folder), ['Missing punches'], fixture.payroll, tips=fixture.tips)
            for msg, expected in zip(messages, ['EPIZKT00.CSV', 'EPIFNC00.CSV', 'EPIFND00.CSV']):
                attachments = list(msg.iter_attachments())
                self.assertEqual(attachments[0].get_filename(), expected)
                self.assertEqual(attachments[0].get_content_type(), 'text/csv')
                self.assertFalse(attachments[0].get_payload(decode=True).startswith(b'PK'))
                self.assertEqual(msg['X-ADP-Import-Status'], 'incomplete')
                self.assertIn('Blank amounts are unverified, not zero', msg.get_body().get_content())
                self.assertTrue(any('issues' in a.get_filename() for a in attachments))
            co = next(m for m in messages if m['Subject'].startswith('CO'))
            raw = next(co.iter_attachments()).get_payload(decode=True)
            self.assertIn(b'13.00,1.50,', raw)  # All stores combined once; unknown tips blank.

    def test_invalid_template_does_not_fabricate_csv(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'ADP_TX_TEMPLATE': 'broken'}, clear=True):
            name, audit = prepare_candidate('3910', self.people, self.dates, Path(folder))
            self.assertIsNone(name)
            self.assertIn('validation failed', audit['issues'][0])


if __name__ == '__main__':
    unittest.main()
