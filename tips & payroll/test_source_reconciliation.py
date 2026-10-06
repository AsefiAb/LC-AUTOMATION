import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from source_reconciliation import reconcile, hours_fingerprint, completion_code


class SourceReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.stores = ['3356-01', '3759-01', '3910-01']
        self.payroll = {'period': {'start_date': '2026-09-08', 'end_date': '2026-09-21'},
                        'roster': [{'geid': '1'}, {'geid': '2'}], 'employees': [
                            {'store': '3356-01', 'employee_id': '1', 'regular_hours': '10.00', 'overtime_hours': '2.00'},
                            {'store': '3759-01', 'employee_id': '1', 'regular_hours': '3.00', 'overtime_hours': '0.00'},
                            {'store': '3910-01', 'employee_id': '2', 'regular_hours': '0.00', 'overtime_hours': '0.00'}]}
        self.evidence = {'source': 'Altametrics', 'period': self.payroll['period'].copy(),
                         'tcp_hours_sha256': hours_fingerprint(self.payroll),
                         'coverage': {s: {'complete': True, 'evidence_ref': 'private-source-report.csv'} for s in self.stores},
                         'records': [dict(store=p['store'], geid=p['employee_id'], regular_hours=p['regular_hours'],
                                          overtime_hours=p['overtime_hours']) for p in self.payroll['employees']]}

    def test_missing_evidence_never_means_zero_or_complete(self):
        audit = reconcile(self.payroll, self.stores)
        self.assertFalse(audit['ready'])
        self.assertEqual(len(audit['stores']), 3)
        self.assertTrue(all(p['issues'] for p in audit['stores'].values()))

    def test_matched_sources_do_not_change_payroll(self):
        before = copy.deepcopy(self.payroll)
        self.assertTrue(reconcile(self.payroll, self.stores, self.evidence)['ready'])
        self.assertEqual(self.payroll, before)

    def test_current_and_future_tcp_hours_are_authoritative(self):
        for dates in [('2026-09-22', '2026-10-05'), ('2026-10-06', '2026-10-19')]:
            self.payroll['period'] = dict(start_date=dates[0], end_date=dates[1])
            before = copy.deepcopy(self.payroll)
            audit = reconcile(self.payroll, self.stores, self.evidence)
            self.assertTrue(audit['ready'])
            self.assertFalse(audit['source_policy']['independent_hours_comparison_required'])
            self.assertFalse(audit['informational_comparison']['ready'])
            self.assertTrue(all(not p['issues'] for p in audit['stores'].values()))
            self.assertEqual(self.payroll, before)

    def test_altametrics_only_hours_are_not_ignored(self):
        self.evidence['records'][2]['regular_hours'] = '24.63'
        audit = reconcile(self.payroll, self.stores, self.evidence)
        self.assertFalse(audit['ready'])
        self.assertIn('24.63', audit['stores']['3910-01']['issues'][0])
        self.assertTrue(audit['stores']['3759-01']['ready'])

    def test_tcp_only_hours_and_ot_disagreements_are_flagged(self):
        self.evidence['records'].pop(0)
        self.assertFalse(reconcile(self.payroll, self.stores, self.evidence)['ready'])
        self.evidence['records'].append(dict(store='3356-01', geid='1', regular_hours='12.00', overtime_hours='0.00'))
        self.assertFalse(reconcile(self.payroll, self.stores, self.evidence)['ready'])

    def test_incomplete_coverage_unmapped_ids_duplicates_and_bad_hours_fail(self):
        for mutation in [lambda e: e['coverage'].pop('3910-01'),
                         lambda e: e['records'][0].update(geid='unknown'),
                         lambda e: e['records'].append(e['records'][0].copy()),
                         lambda e: e['records'][0].update(regular_hours='10:49')]:
            evidence = copy.deepcopy(self.evidence)
            mutation(evidence)
            self.assertFalse(reconcile(self.payroll, self.stores, evidence)['ready'])

    def test_prior_period_or_changed_tcp_hours_invalidate_evidence(self):
        self.evidence['period']['end_date'] = '2026-09-20'
        self.assertFalse(reconcile(self.payroll, self.stores, self.evidence)['ready'])
        self.evidence['period'] = self.payroll['period'].copy()
        self.payroll['employees'][0]['regular_hours'] = '11.00'
        self.assertFalse(reconcile(self.payroll, self.stores, self.evidence)['ready'])

    def test_deliverable_does_not_mean_complete(self):
        self.assertEqual(completion_code({'ready': False, 'reports_deliverable': True}), 2)
        self.assertEqual(completion_code({'ready': True, 'reports_deliverable': True}), 0)
        self.assertEqual(completion_code({'ready': True}), 2)

    def test_package_keeps_all_regions_incomplete_without_source_evidence(self):
        from payroll_run import STORES
        from report_package import prepare_current, verify_stores
        from test_adp_roster import CurrentADPRosterTests
        fixture = CurrentADPRosterTests()
        fixture.setUp()
        payroll = copy.deepcopy(self.payroll)
        payroll.update(verified=True, issues=[])
        payroll['roster'] = [dict(geid='1', first_name='Robin', last_name='De La Cruz', roles=['crew']),
                             dict(geid='2', first_name='New', last_name='Person', roles=['crew'])]
        payroll['employees'][-1]['employee_id'] = '1'
        for p in payroll['employees']:
            p.update(name='Robin De La Cruz', roles=['crew'],
                     total_hours=f"{float(p['regular_hours']) + float(p['overtime_hours']):.2f}")
        gateway = dict(period=payroll['period'], verified=True, issues=[],
                       stores=[dict(store=s, total_tips='0.00') for s in STORES])
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}):
            templates = Path(folder) / 'templates'
            templates.mkdir()
            for company in ('FND', 'FNC', 'ZKT'):
                (templates / f'EPI{company}00.CSV').write_bytes(fixture.raw.replace(b'ZKT', company.encode()))
            output = Path(folder) / 'reports'
            messages, data, audit = prepare_current(gateway, payroll, output, templates, {'allow_missing_zero': True})
            self.assertFalse(audit['ready'])
            self.assertTrue(audit['reports_deliverable'])
            self.assertEqual(completion_code(audit), 2)
            self.assertEqual(len(audit['source_reconciliation']['stores']), 18)
            self.assertEqual(len(messages), 3)
            for message in messages:
                self.assertIn('INCOMPLETE PAYROLL', message['Subject'])
                self.assertIn('not fully reconciled', message.get_body().get_content())
                filename = next(message.iter_attachments()).get_filename()
                self.assertTrue((output / 'NEEDS REVIEW' / filename).exists())
            verify_stores(data, output)
            # Only independent, complete source evidence can clear this check.
            evidence = dict(source='Altametrics', period=payroll['period'],
                            tcp_hours_sha256=hours_fingerprint(payroll),
                            coverage={s: dict(complete=True, evidence_ref='synthetic-test-source.csv') for s in STORES},
                            records=[dict(store=p['store'], geid=p['employee_id'],
                                          regular_hours=p['regular_hours'], overtime_hours=p['overtime_hours'])
                                     for p in payroll['employees']])
            _, _, ready = prepare_current(gateway, payroll, Path(folder) / 'matched', templates,
                                           {'allow_missing_zero': True, 'source_reconciliation': evidence})
            self.assertEqual(completion_code(ready), 0)
