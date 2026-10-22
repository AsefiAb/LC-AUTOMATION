import unittest.mock
import copy
import json
import os
import unittest
from unittest.mock import patch

from allocate_tips import allocate
from role_reviews import apply_role_reviews


class RoleReviewTests(unittest.TestCase):
    def setUp(self):
        self.person = dict(geid='123', roles=['crew member'])
        self.payroll = dict(period={'start_date': '2026-09-22', 'end_date': '2026-10-05'},
                            verified=True, issues=[], roster=[self.person], employees=[
                                dict(employee_id='123', store='3356-01', roles=['crew member'], total_hours='12.00')])
        self.registry = dict(version=1, unresolved=[dict(geid='123', source_role='co-manager',
                                                        evidence_ref='private independent source report')])

    def apply(self, registry):
        with patch.dict(os.environ, {'PAYROLL_ROLE_REVIEWS': json.dumps(registry)}):
            return apply_role_reviews(copy.deepcopy(self.payroll))

    def test_conflict_blocks_tips_without_changing_tcp_roles_or_hours(self):
        result = self.apply(self.registry)
        self.assertFalse(result['verified'])
        self.assertEqual(result['employees'], self.payroll['employees'])
        self.assertEqual(result['roster'], self.payroll['roster'])
        self.assertIn('independent source previously: co-manager', result['issues'][0])
        self.assertEqual(result['role_reviews']['unresolved'][0]['tcp_roles'], ['crew member'])
        gateway = dict(period=result['period'], verified=True, issues=[], stores=[dict(store='3356-01', total_tips='5.00')])
        with self.assertRaisesRegex(ValueError, 'verified'):
            allocate(gateway, result)

    def test_no_automatic_clearance_from_changed_tcp_role(self):
        self.payroll['roster'][0]['roles'] = ['co-manager']
        self.assertFalse(self.apply(self.registry)['verified'])

    def test_missing_malformed_duplicate_registry_blocks(self):
        for text in ('', 'invalid', '{}', '[]'):
            with patch.dict(os.environ, {'PAYROLL_ROLE_REVIEWS': text}):
                self.assertFalse(apply_role_reviews(copy.deepcopy(self.payroll))['verified'])
        self.registry['unresolved'].append(copy.deepcopy(self.registry['unresolved'][0]))
        self.assertFalse(self.apply(self.registry)['verified'])

    def test_reviewed_empty_registry_or_absent_employee_adds_no_blocker(self):
        self.assertTrue(self.apply(dict(version=1, unresolved=[]))['verified'])
        self.registry['unresolved'][0]['geid'] = '999'
        self.assertTrue(self.apply(self.registry)['verified'])

    def confirmation(self):
        return dict(geid='123', role='co-manager', period=self.payroll['period'].copy(),
                    evidence_ref='Owner confirmed management for this exact payroll period')

    def test_confirmed_management_override_excludes_tips_preserving_source_hours(self):
        self.registry['confirmed'] = [self.confirmation()]
        self.payroll['roster'].append(dict(geid='456', roles=['crew member']))
        self.payroll['employees'].append(dict(employee_id='456', store='3356-01', roles=['crew member'], total_hours='10.00'))
        result = self.apply(self.registry)
        self.assertTrue(result['verified'])
        self.assertEqual(result['role_reviews']['unresolved'], [])
        self.assertEqual(result['roster'][0]['roles'], ['co-manager'])
        self.assertEqual(result['roster'][0]['tcp_roles'], ['crew member'])
        self.assertEqual(result['employees'][0]['total_hours'], '12.00')
        self.assertEqual(result['employees'][0]['tcp_roles'], ['crew member'])
        self.assertEqual(self.payroll['employees'][0]['roles'], ['crew member'])
        gateway = dict(period=result['period'], verified=True, issues=[], stores=[dict(store='3356-01', total_tips='5.00')])
        allocation = allocate(gateway, result)
        self.assertEqual({p['employee_id']: p['tips'] for p in allocation['employees']}, {'123': '0.00', '456': '5.00'})

    def test_period_specific_confirmation_does_not_silently_clear_future_or_past(self):
        self.registry['confirmed'] = [self.confirmation()]
        for first, last in [('2026-09-08', '2026-09-21'), ('2026-10-06', '2026-10-19')]:
            self.payroll['period'] = dict(start_date=first, end_date=last)
            result = self.apply(self.registry)
            self.assertFalse(result['verified'])
            self.assertEqual(result['roster'][0]['roles'], ['crew member'])
            self.assertEqual(result['role_reviews']['confirmed'], [])

    def test_bad_confirmation_or_crew_override_never_clears_review(self):
        for mutation in [lambda p: p.update(role='crew'),
                         lambda p: p.update(geid='not-an-open-review'),
                         lambda p: p.update(evidence_ref=''),
                         lambda p: p.update(period={}),
                         lambda p: p['period'].update(end_date='2026-10-06')]:
            confirmed = self.confirmation()
            mutation(confirmed)
            self.registry['confirmed'] = [confirmed]
            result = self.apply(self.registry)
            self.assertFalse(result['verified'])
            self.assertEqual(result['roster'][0]['roles'], ['crew member'])
        self.registry['confirmed'] = [self.confirmation(), self.confirmation()]
        self.assertFalse(self.apply(self.registry)['verified'])


    def test_owner_confirmed_crew_or_trainee_without_open_review_is_accepted(self):
        import json, os, role_reviews
        period = {'start_date': '2026-10-06', 'end_date': '2026-10-19'}
        value = {'version': 1, 'unresolved': [], 'confirmed': [
            {'geid': '9000001', 'role': 'crew member', 'period': period, 'evidence_ref': 'owner'},
            {'geid': '9000002', 'role': 'manager in training', 'period': period, 'evidence_ref': 'owner'}]}
        with unittest.mock.patch.dict(os.environ, {'PAYROLL_ROLE_REVIEWS': json.dumps(value)}):
            self.assertEqual(role_reviews.confirmed_roles_for_period(period),
                             {'9000001': 'crew member', '9000002': 'manager in training'})

if __name__ == '__main__':
    unittest.main()
