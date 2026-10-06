import copy
import unittest

from store_readiness import review_allocations


class StoreReadinessTests(unittest.TestCase):
    def setUp(self):
        self.stores = ['3356-01', '3356-02', '3356-03']
        self.period = dict(start_date='2026-09-22', end_date='2026-10-05')
        self.rows = [dict(store=s, geid=str(i), roles=['crew'], regular_hours='10.00',
                          overtime_hours='0.00', total_hours='10.00', tips=None)
                     for i, s in enumerate(self.stores, 1)]
        self.payroll = dict(period=self.period, verified=True, issues=[], roster=[dict(geid=str(i)) for i in range(1, 4)],
                            employees=[{**p, 'employee_id': p['geid']} for p in self.rows])
        self.tips = dict(period=self.period, verified=True, issues=[],
                         stores=[dict(store=s, total_tips='10.01') for s in self.stores])

    def run_allocation(self, report=None):
        return review_allocations(self.payroll, report, self.rows, self.tips, self.stores)

    def test_current_tcp_hours_need_no_adp_or_altametrics_for_xlsx_tips(self):
        audit = self.run_allocation()
        self.assertTrue(audit['all_allocated'])
        self.assertEqual([p['tips'] for p in self.rows], ['10.01'] * 3)

    def test_role_conflict_only_blocks_that_persons_stores(self):
        self.payroll.update(verified=False, issues=['GEID 2: unresolved job-title review.'])
        audit = self.run_allocation()
        self.assertEqual([p['tips'] for p in self.rows], ['10.01', None, '10.01'])
        self.assertFalse(audit['all_allocated'])
        self.assertIn('unresolved job-title', audit['stores']['3356-02']['issues'][0])

    def test_unknown_global_issue_blocks_every_pool(self):
        self.payroll.update(verified=False, issues=['Private role-review registry is missing or invalid.'])
        self.assertFalse(self.run_allocation()['all_allocated'])
        self.assertTrue(all(p['tips'] is None for p in self.rows))

    def test_historical_source_requirement_is_preserved(self):
        self.period.update(start_date='2026-09-08', end_date='2026-09-21')
        self.assertFalse(self.run_allocation()['all_allocated'])
        self.assertTrue(all(p['tips'] is None for p in self.rows))

    def test_open_punch_blocks_cross_store_overtime_exposure_only(self):
        self.rows[1]['geid'] = '1'
        report = dict(period=dict(startDate=self.period['start_date'], stopDate=self.period['end_date']),
                      generated_at='2026-10-06T16:00:00Z', errors={}, datasets={})
        source = dict(locations=[dict(name=s) for s in self.stores],
                      employees=[dict(employeeId=1)], jobcodes=[dict(jobCodeId=1, locationName='3356-01', description='Crew Member')],
                      hours=[dict(employee=dict(employeeId=1), segments=[dict(recordId=9, timeIn='2026-10-05T09:00:00',
                                   timeOut=None, jobCode=dict(jobCodeId=1), hours=dict(regular=0, overtime1=0, overtime2=0))])],
                      worksegments=[dict(id=9, employeeId=1, jobCodeId=1, timeIn='2026-10-05T09:00:00')])
        report['datasets'] = {k: dict(complete=True, records=v) for k, v in source.items()}
        self.payroll.update(verified=False, issues=['TCP check requires correction: open_punches (1)'])
        audit = self.run_allocation(report)
        self.assertEqual([p['tips'] for p in self.rows], [None, None, '10.01'])
        self.assertIn('2026-10-05', audit['stores']['3356-01']['issues'][0])

    def test_manager_elsewhere_cannot_receive_crew_tips(self):
        self.rows.append(dict(store='3356-02', geid='1', roles=['manager'], regular_hours='2.00',
                              overtime_hours='0.00', total_hours='2.00', tips=None))
        self.rows.append(dict(store='3356-01', geid='4', roles=['crew'], regular_hours='10.00',
                              overtime_hours='0.00', total_hours='10.00', tips=None))
        self.assertTrue(self.run_allocation()['all_allocated'])
        self.assertEqual(self.rows[0]['tips'], '0.00')
        self.assertEqual(self.rows[-1]['tips'], '10.01')

    def test_missing_hours_and_duplicate_gateway_pools_remain_blocking(self):
        self.rows[1]['regular_hours'] = None
        self.run_allocation()
        self.assertEqual([p['tips'] for p in self.rows], ['10.01', None, '10.01'])
        for p in self.rows:
            p['tips'] = None
        self.tips['stores'].append(self.tips['stores'][0].copy())
        self.assertFalse(self.run_allocation()['all_allocated'])
        self.assertTrue(all(p['tips'] is None for p in self.rows))


if __name__ == '__main__':
    unittest.main()
