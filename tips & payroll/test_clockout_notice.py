import copy
import unittest
from clockout_notice import build_message


class ClockoutNoticeTests(unittest.TestCase):
    def report(self):
        return dict(period=dict(startDate='2026-09-22', stopDate='2026-10-05'),
            generated_at='2026-10-06T19:00:00+00:00', errors={}, datasets={
                'locations': dict(complete=True, records=[dict(name='03910-00003')]),
                'jobcodes': dict(complete=True, records=[dict(jobCodeId=1, locationName='03910-00003', description='Crew')]),
                'employees': dict(complete=True, records=[dict(employeeId=12, employeeName='Test Worker')]),
                'hours': dict(complete=True, records=[dict(employee=dict(employeeId=12, employeeName='Test Worker'), segments=[
                    dict(recordId=123, timeIn='2026-10-05T15:24:00', timeOut='', jobCode=dict(jobCodeId=1),
                         hours=dict(regular='8:00', overtime1='10:00', overtime2='0:00'))])]),
                'worksegments': dict(complete=True, records=[dict(id=123, employeeId=12, jobCodeId=1, timeIn='2026-10-05T15:24:00')])})

    def test_one_plain_text_email_no_attachments_no_guessed_hours(self):
        report = self.report()
        original = copy.deepcopy(report)
        msg, rows = build_message(report)
        self.assertEqual(len(rows), 1)
        self.assertEqual(msg['From'], 'ab@caazllc.com')
        self.assertEqual(msg['To'], 'alicia@lccollc.com')
        body = msg.get_content()
        self.assertIn('03:24 PM', body)
        self.assertIn('Test Worker', body)
        self.assertIn('3910-03', body)
        self.assertNotIn('10:00', body)
        self.assertEqual(list(msg.iter_attachments()), [])
        self.assertEqual(report, original)

    def test_incomplete_source_cannot_send_a_misleading_list(self):
        report = self.report()
        report['datasets']['hours']['complete'] = False
        with self.assertRaises(ValueError):
            build_message(report)

    def test_closed_punch_is_removed_but_api_only_open_punch_is_retained(self):
        report = self.report()
        report['datasets']['hours']['records'][0]['segments'][0]['timeOut'] = '2026-10-05T22:00:00'
        _, rows = build_message(report)
        self.assertIn('differs', rows[0]['detail'])
        report['datasets']['worksegments']['records'][0]['timeOut'] = '2026-10-05T22:00:00'
        msg, rows = build_message(report)
        self.assertEqual(rows, [])
        self.assertIn('no missing clock-outs', msg.get_content())


if __name__ == '__main__':
    unittest.main()
