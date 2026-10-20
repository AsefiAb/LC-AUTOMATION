import unittest

import adp_api
import adp_roster_check as check


def worker(oid, first, last, company, number, status='Active'):
    return {'associateOID': oid, 'person': {'legalName': {'givenName': first, 'familyName1': last}},
            'workerStatus': {'statusCode': {'codeValue': status}},
            'workAssignments': [{'payrollGroupCode': company, 'payrollFileNumber': number,
                                 'assignmentStatus': {'statusCode': {'codeValue': 'A' if status == 'Active' else 'T'}},
                                 'jobTitle': 'Crew', 'hireDate': '2026-10-01'}]}


class RosterCheckTests(unittest.TestCase):
    def test_new_hire_terminated_and_tcp_only_people_are_flagged(self):
        workers = [r for w in (worker('1', 'Ana', 'Diaz', 'FND', '000101'),
                               worker('2', 'Ben', 'Lee', 'FND', '000102'),
                               worker('3', 'Cy', 'Old', 'FND', '000103', status='Terminated'))
                   for r in adp_api.worker_rows(w)]
        templates = {'CO': {'000101': ['FND', 'B1', '000101', '', 'Diaz, Ana'],
                            '000103': ['FND', 'B1', '000103', '', 'Old, Cy']}}
        payroll = {'employees': [{'employee_id': 9, 'name': 'Ana Diaz', 'store': '3356-01'},
                                 {'employee_id': 8, 'name': 'Dee New', 'store': '3356-02'}]}
        report = check.compare(workers, templates, payroll)['CO']
        self.assertEqual(report['adp_active'], 2)
        self.assertEqual([r['name'] for r in report['missing_from_epi']], ['Ben Lee'])
        self.assertEqual([r['file_number'] for r in report['epi_not_active_in_adp']], ['000103'])
        self.assertEqual([r['name'] for r in report['tcp_not_in_adp']], ['Dee New'])
        self.assertEqual(report['adp_active_without_tcp_hours'], ['Ben Lee'])

    def test_pem_rejects_non_pem_values(self):
        self.assertTrue(adp_api.pem('-----BEGIN X-----\nabc\n-----END X-----').startswith('-----BEGIN X-----\n'))
        with self.assertRaises(adp_api.ADPError):
            adp_api.pem('not a certificate')


if __name__ == '__main__':
    unittest.main()
