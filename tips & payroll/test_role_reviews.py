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


if __name__ == '__main__':
    unittest.main()
