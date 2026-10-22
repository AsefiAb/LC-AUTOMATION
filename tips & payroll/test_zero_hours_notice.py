"""zero_hours_notice.build / build_message. Every name, TCP ID and ADP file number here is invented."""
import json
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'sales automation'))

import zero_hours_notice as z

DATES = {'start_date': '2026-10-06', 'end_date': '2026-10-19'}


def tcp(geid, first, last, region, store, hired='2025-01-01', terminated='', suspended=False):
    return {'employeeId': geid, 'firstName': first, 'lastName': last, 'roleId': region,
            'location': store, 'hireDate': hired + 'T00:00:00', 'terminationDate': terminated, 'isSuspended': suspended}


def adp(oid, first, last, company, number, hired='2025-01-01', status='Active', assignment='A', title='TEAM MEMBER',
        location='', primary=True, preferred=''):
    return {'associate_oid': oid, 'first_name': first, 'last_name': last, 'preferred_name': preferred,
            'worker_status': status, 'assignment_status': assignment, 'primary': primary, 'company_code': company,
            'file_number': number, 'job_title': title, 'location': location, 'hire_date': hired,
            'termination_date': '', 'hourly_rate': ''}


def punch(geid, day='2026-10-08'):
    return {'employeeId': geid, 'timeIn': day + 'T10:00:00', 'timeOut': None}


def report_of(employees, punches):
    return {'period': {'startDate': DATES['start_date'], 'stopDate': DATES['end_date']}, 'errors': {},
            'datasets': {'employees': {'complete': True, 'records': employees},
                         'worksegments': {'complete': True, 'records': punches}}}


def ident(geid, company, number, tcp_name, adp_name, ref='Owner confirmation 2026-01-01'):
    return {'geid': geid, 'company': company, 'file_number': number, 'tcp_name': tcp_name,
            'adp_name': adp_name, 'evidence_ref': ref}


def report():
    employees = [tcp(1, 'Ana', 'Diaz', 'AZ', '03759-00001'),
                 tcp(2, 'Ben', 'Lee', 'AZ', '03759-00002'),
                 tcp(3, 'Cy', 'New', 'AZ', '03759-00003', hired='2026-10-10'),
                 tcp(4, 'Tori', 'Mclaren', 'TX', '03910-00002'),
                 tcp(5, 'Ed', 'Gone', 'TX', '03910-00001'),
                 tcp(6, 'Fay', 'Ghost', 'TX', '03910-00001'),
                 tcp(7, 'Pat', 'Exampel', 'AZ', '03759-02'),
                 tcp(8, 'Dee', 'Boss', 'TX', '03910-00004'),
                 tcp(9, 'Old', 'Leaver', 'TX', '03910-00004', terminated='2026-09-01'),
                 tcp(10, 'Sus', 'Pended', 'TX', '03910-00004', suspended=True),
                 tcp(11, 'Amy', 'Zulu', 'CO', '03356-00001'),
                 tcp(12, 'Zed', 'Able', 'CO', '03356-00002'),
                 tcp(13, 'Rhea', 'Hire', 'CO', '03356-00003')]
    punches = [punch(1, '2026-10-07'), punch(4, '2026-10-15'), punch(7), punch(13, '2026-10-09'),
               punch(2, '2026-10-01')]  # Before the period: still 0 hours.
    return report_of(employees, punches)


def workers():
    return [adp('A1', 'Ana', 'Diaz', 'FNC', '010105'),
            adp('A2', 'Ben', 'Lee', 'FNC', '010102', location='37592'),
            adp('A3', 'Cy', 'New', 'FNC', '010103', hired='2026-10-10'),
            adp('A4', 'Tori', 'Mclarin', 'ZKT', '000921'),
            adp('A5', 'Ed', 'Gone', 'ZKT', '000050', status='Terminated', assignment='T'),
            adp('A7', 'Pat', 'Example', 'FNC', '099901'),
            adp('A8', 'Dee', 'Boss', 'ZKT', '000127', title='DISTRICT MANAGER'),
            adp('A9', 'Office', 'Person', 'FND', '095706', title='ASSISTANT MANAGER', location='11159'),
            adp('A10', 'Rhea', 'Hire', 'FND', '096327', hired='2026-07-01', location='33563'),
            adp('A10', 'Rhea', 'Hire', 'FND', '056397', hired='2023-10-22', location='33563', primary=False),
            adp('A11', 'Amy', 'Zulu', 'FND', '096001'),
            adp('A12', 'Zed', 'Able', 'FND', '096002'),
            adp('A13', 'Blank', 'Number', 'FND', '')]


CONFIRMED = [ident('7', 'FNC', '099901', 'Pat Exampel', 'Pat Example')]


def names(data, code, kind):
    return [r['name'] for r in data[code][kind]]


def notes_of(data, code, kind):
    return [(r['name'], r['note']) for r in data[code][kind]]


def pairs(data, code):
    return [(r['adp_name'], r['file_number'], r['tcp_name'], r['note']) for r in data[code]['check_names']]


class ZeroHoursTests(unittest.TestCase):
    def build(self, employees, punches, adp_rows, identities=()):
        notes = []
        data = z.build(DATES, report_of(employees, punches), adp_rows, identities, notes)
        return data, notes

    def test_lists_only_store_employees_without_punches_and_hired_before_the_period(self):
        notes = []
        data = z.build(DATES, report(), workers(), CONFIRMED, notes)
        self.assertEqual(notes, [])
        self.assertEqual(names(data, 'AZ', 'zero_hours'), ['Ben Lee'])  # Diaz worked, New hired in period, Exampel confirmed and worked.
        self.assertEqual(data['AZ']['zero_hours'][0]['store'], '3759-02')
        self.assertEqual(names(data, 'CO', 'zero_hours'), ['Zed Able', 'Amy Zulu'])  # Last name, then first.
        self.assertNotIn('Office Person', names(data, 'CO', 'zero_hours'))         # Office location, not a store.
        self.assertEqual(names(data, 'TX', 'zero_hours'), [])                      # District manager excluded; Mclarin worked as Mclaren.
        self.assertEqual(pairs(data, 'TX'), [('Tori Mclarin', '000921', 'Tori Mclaren', '')])
        self.assertEqual(notes_of(data, 'TX', 'tcp_only'), [('Fay Ghost', 'not found in ADP'), ('Ed Gone', 'terminated in ADP')])
        self.assertEqual(notes_of(data, 'CO', 'duplicates'), [('Rhea Hire', 'more than one active ADP record; payroll uses #096327')])
        self.assertEqual(data['CO']['duplicates'][0]['file_number'], '056397')

    def test_every_value_is_a_list_and_the_result_is_json(self):
        data = z.build(DATES, report(), workers(), CONFIRMED)
        self.assertEqual(sorted(data), ['AZ', 'CO', 'TX'])
        for lists in data.values():
            self.assertEqual(sorted(lists), sorted(z.LISTS))
            self.assertTrue(all(isinstance(v, list) for v in lists.values()))
        json.dumps(data)
        self.assertIsInstance(z.build(DATES, report(), workers()), dict)  # notes and identities are optional.

    def test_any_punch_in_the_period_counts_as_worked(self):
        data, _ = self.build([tcp(1, 'Ana', 'Diaz', 'AZ', '03759-00001'), tcp(2, 'Ben', 'Lee', 'AZ', '03759-00002')],
                             [{'employeeId': 1, 'timeIn': '2026-10-19T23:00:00', 'timeOut': None},  # No calculated hours.
                              {'employeeId': 2, 'timeIn': '2026-10-20T08:00:00'}],                  # After the period.
                             [adp('A1', 'Ana', 'Diaz', 'FNC', '010105'), adp('A2', 'Ben', 'Lee', 'FNC', '010102')])
        self.assertEqual(names(data, 'AZ', 'zero_hours'), ['Ben Lee'])

    def test_people_hired_during_the_period_are_left_out_on_both_sides(self):
        data, _ = self.build([tcp(1, 'Nia', 'Fresh', 'AZ', '03759-00001', hired='2026-10-12')], [],
                             [adp('A1', 'Kai', 'Newer', 'FNC', '010111', hired='2026-10-06')])
        self.assertEqual(data['AZ']['zero_hours'], [])
        self.assertEqual(data['AZ']['tcp_only'], [])

    def test_lists_are_sorted_by_last_name_then_first_name(self):
        data, _ = self.build([], [], [adp('A1', 'Cal', 'Moss', 'FNC', '010121'), adp('A2', 'Ann', 'Baker', 'FNC', '010122'),
                                      adp('A3', 'Abe', 'Moss', 'FNC', '010123')])
        self.assertEqual(names(data, 'AZ', 'zero_hours'), ['Ann Baker', 'Abe Moss', 'Cal Moss'])

    # Confirmed identities ------------------------------------------------------------------

    def test_confirmed_file_terminated_and_newer_record_active_is_asked_not_zero(self):
        employees = [tcp(7, 'Pat', 'Exampel', 'AZ', '03759-02')]
        rows = [adp('A7', 'Pat', 'Example', 'FNC', '099901', status='Terminated', assignment='T'),
                adp('A70', 'Pat', 'Example', 'FNC', '099977', hired='2026-08-01')]
        data, notes = self.build(employees, [punch(7)], rows, CONFIRMED)
        self.assertEqual(data['AZ']['zero_hours'], [])
        self.assertEqual(pairs(data, 'AZ'), [('Pat Example', '099977', 'Pat Exampel',
                                             "TCP person's confirmed ADP #099901 is terminated in ADP")])
        self.assertEqual(notes, ['AZ GEID 7: confirmed ADP file 099901 is terminated or not active in ADP; match not applied.'])

    def test_confirmed_older_assignment_moves_to_the_newest_record(self):
        employees = [tcp(7, 'Pat', 'Exampel', 'AZ', '03759-02')]
        rows = [adp('A7', 'Pat', 'Example', 'FNC', '099901', hired='2024-02-01'),
                adp('A7', 'Pat', 'Example', 'FNC', '099988', hired='2026-08-01', primary=False)]
        data, notes = self.build(employees, [punch(7)], rows, CONFIRMED)
        self.assertEqual(data['AZ']['zero_hours'], [])  # Worked: the newest record is never listed as 0 hours.
        self.assertEqual(data['AZ']['check_names'], [])
        self.assertEqual([(r['file_number'], r['current_file']) for r in data['AZ']['duplicates']], [('099901', '099988')])
        self.assertEqual(notes, ['AZ GEID 7: confirmed ADP file 099901 is an older assignment of the same ADP person; '
                                 'using the newest ADP file 099988.'])
        data, _ = self.build(employees, [], rows, CONFIRMED)  # No punches: the newest record is the 0-hours one.
        self.assertEqual([(r['file_number'], r['note'], r['store']) for r in data['AZ']['zero_hours']],
                         [('099988', '', '3759-02')])
        self.assertEqual(data['AZ']['tcp_only'], [])

    def test_confirmed_leaver_terminated_in_adp_is_listed_in_tcp_only(self):
        employees = [tcp(7, 'Pat', 'Exampel', 'AZ', '03759-02')]
        rows = [adp('A7', 'Pat', 'Example', 'FNC', '099901', status='Terminated', assignment='T')]
        data, notes = self.build(employees, [], rows, CONFIRMED)
        self.assertEqual(notes_of(data, 'AZ', 'tcp_only'), [('Pat Exampel', 'terminated in ADP (#099901)')])
        self.assertEqual(data['AZ']['zero_hours'], [])
        self.assertEqual(len(notes), 1)

    def test_confirmed_file_missing_from_adp_is_listed_in_tcp_only(self):
        data, notes = self.build([tcp(7, 'Pat', 'Exampel', 'AZ', '03759-02')], [], [], CONFIRMED)
        self.assertEqual(notes_of(data, 'AZ', 'tcp_only'), [('Pat Exampel', 'confirmed ADP #099901 not found in ADP')])
        self.assertEqual(notes, ['AZ GEID 7: confirmed ADP file 099901 is not in ADP; match not applied.'])

    def test_stale_confirmation_is_never_matched_to_an_unrelated_record_by_name(self):
        # TCP name equals another active ADP person's name, but the owner confirmed a now-terminated record.
        employees = [tcp(7, 'Pat', 'Example', 'AZ', '03759-02')]
        rows = [adp('A7', 'Pat', 'Example', 'FNC', '099901', status='Terminated', assignment='T'),
                adp('A55', 'Pat', 'Example', 'FNC', '099955')]
        data, _ = self.build(employees, [], rows, [ident('7', 'FNC', '099901', 'Pat Example', 'Pat Example')])
        self.assertEqual(notes_of(data, 'AZ', 'tcp_only'), [('Pat Example', 'terminated in ADP (#099901)')])
        self.assertEqual(notes_of(data, 'AZ', 'zero_hours'), [('Pat Example', 'in TCP as Pat Example')])

    def test_confirmed_geid_missing_from_tcp_is_noted_and_never_raises(self):
        rows = [adp('A30', 'Quinn', 'Solo', 'FNC', '099930'), adp('A7', 'Pat', 'Example', 'FNC', '099901')]
        identities = [ident('999', 'FNC', '099930', 'Quinn Solo', 'Quinn Solo'),
                      ident('998', 'FNC', '099901', 'Pat Example', 'Pat Example')]
        data, notes = self.build([tcp(7, 'Pat', 'Exampel', 'AZ', '03759-02')], [punch(7)], rows, identities)
        self.assertEqual(notes_of(data, 'AZ', 'zero_hours'),
                         [('Quinn Solo', 'not found in TCP; confirmed TCP ID 999 is not in TCP')])
        self.assertEqual([r['file_number'] for r in data['AZ']['check_names']], ['099901'])  # A lookalike worked.
        self.assertEqual(notes, ['AZ GEID 999: confirmed TCP ID is not in the TCP employee list; match not applied.',
                                 'AZ GEID 998: confirmed TCP ID is not in the TCP employee list; match not applied.'])

    def test_confirmed_file_that_now_belongs_to_another_name_is_not_applied(self):
        employees = [tcp(7, 'Pat', 'Exampel', 'AZ', '03759-02')]
        rows = [adp('A7', 'Pat', 'Example', 'FNC', '099901'), adp('A2', 'Quinn', 'Other', 'FNC', '099902')]
        typo = [ident('7', 'FNC', '099902', 'Pat Exampel', 'Pat Example')]
        data, notes = self.build(employees, [punch(7)], rows, typo)
        self.assertEqual(notes_of(data, 'AZ', 'zero_hours'), [('Quinn Other', 'not found in TCP')])
        self.assertEqual(pairs(data, 'AZ'), [('Pat Example', '099901', 'Pat Exampel', '')])
        self.assertEqual(notes, ['AZ GEID 7: confirmed ADP file 099902 no longer has the confirmed ADP name in ADP; '
                                 'match not applied.'])
        spacing = [ident('7', 'FNC', '99901', 'pat  EXAMPEL', ' pat   EXAMPLE ')]
        data, notes = self.build(employees, [punch(7)], rows, spacing)  # Case and spacing aside: applied.
        self.assertEqual(names(data, 'AZ', 'zero_hours'), ['Quinn Other'])
        self.assertEqual((data['AZ']['check_names'], data['AZ']['tcp_only'], notes), ([], [], []))

    def test_saved_settings_identity_counts_like_a_confirmed_match(self):
        employees = [tcp(123, 'Kim', 'Vance', 'AZ', '03759-01')]
        rows = [adp('A60', 'Kimberly', 'Vance-Ortiz', 'FNC', '099960')]
        saved = [ident('123', 'FNC', '99960', 'Kim Vance', 'Kimberly Vance-Ortiz', ref='ADP_ROSTER_SETTINGS')]
        data, notes = self.build(employees, [punch(123)], rows, saved)
        self.assertEqual((data['AZ']['zero_hours'], data['AZ']['check_names'], data['AZ']['tcp_only'], notes),
                         ([], [], [], []))
        data, _ = self.build(employees, [], rows, saved)
        self.assertEqual(notes_of(data, 'AZ', 'zero_hours'), [('Kimberly Vance-Ortiz', '')])
        self.assertEqual(data['AZ']['tcp_only'], [])

    def test_saved_identity_with_the_epi_alternate_name_still_applies(self):
        # Saved ADP_ROSTER_SETTINGS matches carry the EPI spelling, where ADP can append
        # an alternate name in parentheses; payroll accepts that row, so the email must too.
        employees = [tcp(123, 'Kim', 'Vance', 'AZ', '03759-01')]
        rows = [adp('A60', 'Kimberly', 'Quorrel', 'FNC', '099960', preferred='Kimmy')]
        saved = [ident('123', 'FNC', '99960', 'Kim Vance', 'Kimberly Quorrel(Kimmy Quorrel)', ref='ADP_ROSTER_SETTINGS')]
        data, notes = self.build(employees, [punch(123)], rows, saved)
        self.assertEqual((data['AZ']['zero_hours'], data['AZ']['check_names'], data['AZ']['tcp_only'], notes),
                         ([], [], [], []))
        other = [ident('123', 'FNC', '99960', 'Kim Vance', 'Kimberly Quarrel(Kim Quarrel)', ref='ADP_ROSTER_SETTINGS')]
        data, notes = self.build(employees, [punch(123)], rows, other)
        self.assertEqual(notes, ['AZ GEID 123: confirmed ADP file 99960 no longer has the confirmed ADP name in ADP; '
                                 'match not applied.'])

    def test_changed_tcp_name_asks_instead_of_reporting_zero_hours(self):
        employees = [tcp(123, 'Kay', 'Smith', 'AZ', '03759-01')]  # TCP renamed since the confirmation.
        rows = [adp('A60', 'Kimberly', 'Vance-Ortiz', 'FNC', '099960')]
        saved = [ident('123', 'FNC', '099960', 'Kim Vance', 'Kimberly Vance-Ortiz')]
        data, notes = self.build(employees, [punch(123)], rows, saved)
        self.assertEqual(data['AZ']['zero_hours'], [])
        self.assertEqual(pairs(data, 'AZ'), [('Kimberly Vance-Ortiz', '099960', 'Kay Smith',
                                             'TCP name changed since the saved match')])
        self.assertEqual(notes, ['AZ GEID 123: the TCP name no longer agrees with the confirmed TCP name; '
                                 'match not applied.'])

    def test_malformed_or_repeated_identities_are_noted_not_raised(self):
        bad = [dict(CONFIRMED[0], geid=7), CONFIRMED[0], dict(CONFIRMED[0], geid='70')]
        notes = []
        data = z.build(DATES, report(), workers(), bad, notes)
        self.assertEqual(notes, ['A confirmed ADP identity entry is malformed; it was not used.',
                                 'AZ GEID 70: confirmed ADP file 099901 repeats another confirmed match for the same '
                                 'GEID or ADP file; not used.'])
        self.assertEqual(names(data, 'AZ', 'zero_hours'), ['Ben Lee'])

    # Name matching -----------------------------------------------------------------------

    def test_middle_names_and_short_forms_are_asked_not_reported_as_zero_hours(self):
        employees = [tcp(1, 'Lia', 'Senter', 'AZ', '03759-01'), tcp(2, 'Mike', 'Brandt', 'AZ', '03759-01')]
        rows = [adp('A1', 'Lia Rose', 'Senter', 'FNC', '099941'), adp('A2', 'Michael', 'Brandt', 'FNC', '099942')]
        data, _ = self.build(employees, [punch(1), punch(2)], rows)
        self.assertEqual(data['AZ']['zero_hours'], [])
        self.assertEqual([(r['adp_name'], r['tcp_name']) for r in data['AZ']['check_names']],
                         [('Michael Brandt', 'Mike Brandt'), ('Lia Rose Senter', 'Lia Senter')])

    def test_compound_surname_without_punches_is_listed_once_with_the_tcp_name(self):
        data, _ = self.build([tcp(1, 'Rosa', 'Vale', 'AZ', '03759-01')], [],
                             [adp('A1', 'Rosa', 'Marin-Vale', 'FNC', '099943')])
        self.assertEqual(notes_of(data, 'AZ', 'zero_hours'), [('Rosa Marin-Vale', 'in TCP as Rosa Vale')])
        self.assertEqual(data['AZ']['tcp_only'], [])

    def test_shared_last_name_with_a_different_first_initial_is_not_a_lookalike(self):
        data, _ = self.build([tcp(1, 'Ana', 'Ortez', 'AZ', '03759-01')], [punch(1)],
                             [adp('A1', 'Luis', 'Ortez', 'FNC', '099944')])
        self.assertEqual(notes_of(data, 'AZ', 'zero_hours'), [('Luis Ortez', 'not found in TCP')])
        self.assertEqual(data['AZ']['check_names'], [])

    def test_a_matched_person_is_never_a_lookalike_for_another_record(self):
        rows = [adp('A1', 'Dario', 'Pell', 'FNC', '099945'), adp('A2', 'Daria', 'Pell', 'FNC', '099946')]
        data, _ = self.build([tcp(1, 'Dario', 'Pell', 'AZ', '03759-01')], [punch(1)], rows)
        self.assertEqual(notes_of(data, 'AZ', 'zero_hours'), [('Daria Pell', 'not found in TCP')])
        self.assertEqual(data['AZ']['check_names'], [])
        rows = [adp('A1', 'Dario', 'Pell', 'FNC', '099945'),
                adp('A2', 'Daria', 'Pell', 'FNC', '099946', status='Terminated', assignment='T')]
        data, _ = self.build([tcp(1, 'Dario', 'Pell', 'AZ', '03759-01'), tcp(2, 'Daria', 'Pell', 'AZ', '03759-01')],
                             [punch(1)], rows)
        self.assertEqual(notes_of(data, 'AZ', 'tcp_only'), [('Daria Pell', 'terminated in ADP')])
        self.assertEqual(data['AZ']['zero_hours'], [])

    def test_same_name_records_are_asked_when_fewer_people_worked(self):
        rows = [adp('G1', 'Jo', 'Garza', 'FNC', '099962'), adp('G2', 'Jo', 'Garza', 'FNC', '099961')]
        one = [tcp(1, 'Jo', 'Garza', 'AZ', '03759-01')]
        data, _ = self.build(one, [punch(1)], rows)
        self.assertEqual(data['AZ']['zero_hours'], [])
        self.assertEqual([(r['file_number'], r['records'], r['worked'], r['tcp_ids']) for r in data['AZ']['same_name']],
                         [('099961', 2, 1, ['1']), ('099962', 2, 1, ['1'])])
        two = one + [tcp(2, 'Jo', 'Garza', 'AZ', '03759-02')]
        data, _ = self.build(two, [punch(1), punch(2)], rows)
        self.assertEqual((data['AZ']['zero_hours'], data['AZ']['same_name']), ([], []))
        data, _ = self.build(one, [], rows)
        self.assertEqual([r['file_number'] for r in data['AZ']['zero_hours']], ['099961', '099962'])
        self.assertEqual(data['AZ']['same_name'], [])

    def test_duplicate_records_are_listed_separately_with_neutral_wording(self):
        employees = [tcp(13, 'Rhea', 'Hire', 'CO', '03356-00003')]
        rows = [adp('A10', 'Rhea', 'Hire', 'FND', '056397', hired='2023-10-22', location='33563', primary=True),
                adp('A10', 'Rhea', 'Hire', 'FND', '096327', hired='2026-07-31', location='33565', primary=False)]
        for punches in ([punch(13)], []):
            with self.subTest(worked=bool(punches)):
                message, data = z.build_message(DATES, report_of(employees, punches), rows)
                self.assertEqual([(r['file_number'], r['current_file'], r['note']) for r in data['CO']['duplicates']],
                                 [('056397', '096327', 'more than one active ADP record; payroll uses #096327')])
                self.assertNotIn('056397', [r['file_number'] for r in data['CO']['zero_hours']])
                body = message.get_content()
                self.assertIn('- Rhea Hire, Colorado: ADP #056397 (store 3356-03, hired 2023-10-22) and ADP #096327 '
                              '(store 3356-05, hired 2026-07-31); payroll uses #096327', body)
                self.assertNotIn('older', body)
                self.assertNotIn('rehired', body)
        same_day = [dict(rows[0], hire_date='2024-01-01'), dict(rows[1], hire_date='2024-01-01')]
        data = z.build(DATES, report_of(employees, [punch(13)]), same_day)
        self.assertEqual([(r['file_number'], r['current_file']) for r in data['CO']['duplicates']], [('096327', '056397')])

    # Regions -----------------------------------------------------------------------------

    def test_same_name_in_another_region_is_asked_or_annotated(self):
        employees = [tcp(1, 'Sam', 'Cross', 'AZ', '03759-01'),               # Worked in AZ, ADP only in TX.
                     tcp(2, 'Rex', 'Nowhere', 'Manager', ''),                # No TCP region.
                     tcp(3, 'Uma', 'Twin', 'TX', '03910-01'),                # Same ADP person in CO and TX, works in TX.
                     tcp(4, 'Rae', 'Other', 'AZ', '03759-01'),               # No punches, ADP only in TX.
                     tcp(5, 'Lee', 'Park', 'TX', '03910-02'), tcp(6, 'Lee', 'Park', 'AZ', '03759-02'),
                     tcp(7, 'Tess', 'Gone', 'AZ', '03759-01')]               # No punches, terminated in TX ADP.
        rows = [adp('S1', 'Sam', 'Cross', 'ZKT', '000500'),
                adp('R1', 'Rex', 'Nowhere', 'FND', '096100'),
                adp('U1', 'Uma', 'Twin', 'FND', '096200'), adp('U1', 'Uma', 'Twin', 'ZKT', '000700'),
                adp('O1', 'Rae', 'Other', 'ZKT', '000600'),
                adp('P1', 'Lee', 'Park', 'ZKT', '000979'),
                adp('T1', 'Tess', 'Gone', 'ZKT', '000601', status='Terminated', assignment='T')]
        data, notes = self.build(employees, [punch(1), punch(2), punch(3), punch(6)], rows)
        self.assertEqual(pairs(data, 'TX'), [('Sam Cross', '000500', 'Sam Cross',
                                             'same name worked in Arizona TCP; not in Arizona ADP')])
        self.assertEqual(pairs(data, 'CO'), [('Rex Nowhere', '096100', 'Rex Nowhere', 'TCP region unknown')])
        self.assertEqual(notes_of(data, 'CO', 'zero_hours'),
                         [('Uma Twin', 'not in Colorado TCP; same name worked in Texas TCP')])
        self.assertEqual(notes_of(data, 'TX', 'zero_hours'),
                         [('Rae Other', 'not in Texas TCP; same name in Arizona TCP'),
                          ('Lee Park', 'same name worked in Arizona TCP')])
        self.assertEqual(notes_of(data, 'AZ', 'tcp_only'),
                         [('Tess Gone', 'terminated in ADP under Texas'),
                          ('Rae Other', 'not in Arizona ADP; active in ADP under Texas #000600')])
        self.assertEqual(data['AZ']['zero_hours'], [])
        self.assertEqual(notes, [])

    def test_people_without_a_tcp_region_are_noted_not_dropped_silently(self):
        employees = [tcp(1, 'Ann', 'Baker', 'AZ', '03759-01'), tcp(2, 'Vic', 'Roam', 'Manager', '')]
        data, notes = self.build(employees, [], [adp('A1', 'Ann', 'Baker', 'FNC', '010122')])
        self.assertEqual(notes, ['1 TCP people with no punches have no region (role or store) in TCP and were not '
                                 'checked against ADP: TCP IDs 2.'])
        self.assertEqual(sum(len(rows) for lists in data.values() for rows in lists.values()), 1)  # Ann Baker only.

    def test_a_person_is_asked_once_per_record(self):
        # Renamed in TCP since the confirmation, and the new TCP name is exact in another region.
        employees = [tcp(123, 'Kim', 'Vance-Ortiz', 'TX', '03910-01')]
        rows = [adp('A60', 'Kim', 'Vance-Ortiz', 'FNC', '099960')]
        saved = [ident('123', 'FNC', '099960', 'Kim Vance', 'Kim Vance-Ortiz')]
        data, _ = self.build(employees, [punch(123)], rows, saved)
        self.assertEqual(pairs(data, 'AZ'), [('Kim Vance-Ortiz', '099960', 'Kim Vance-Ortiz',
                                             'TCP name changed since the saved match')])
        self.assertEqual(data['AZ']['zero_hours'], [])

    # Email -------------------------------------------------------------------------------

    def test_one_email_to_district_managers_and_kerry_cc_alicia(self):
        message, data = z.build_message(DATES, report(), workers(), CONFIRMED)
        self.assertEqual(message['From'], 'ab@caazllc.com')
        self.assertEqual(message['To'], 'jeff@caazllc.com, caleb@lccollc.com, wilder@lccollc.com, kerry@lccollc.com')
        self.assertEqual(message['Cc'], 'alicia@lccollc.com')
        self.assertEqual(message['Subject'], 'Employees with 0 hours - 10.6.26-10.19.26')
        body = message.get_content()
        self.assertIn('These employees have 0 hours for the last two weeks (10.6.26-10.19.26). '
                      'Please send over termination sheets if they are not employed.', body)
        self.assertLess(body.index('ARIZONA (Jeff)'), body.index('COLORADO (Caleb)'))
        self.assertLess(body.index('COLORADO (Caleb)'), body.index('TEXAS (Wilder)'))
        self.assertIn('ARIZONA (Jeff)\n- Ben Lee, ADP #010102, store 3759-02, hired 2025-01-01\n', body)
        self.assertIn('COLORADO (Caleb)\n- Zed Able, ADP #096002, store 3356-02, hired 2025-01-01\n'
                      '- Amy Zulu, ADP #096001, store 3356-01, hired 2025-01-01\n', body)
        self.assertIn('TEXAS (Wilder)\n- No employees with 0 hours.', body)
        self.assertIn('Please remove them from TCP if they no longer work for us, or send over their paperwork:\n'
                      '- Fay Ghost, Texas, TCP ID 6, store 3910-01, not found in ADP\n'
                      '- Ed Gone, Texas, TCP ID 5, store 3910-01, terminated in ADP\n', body)
        self.assertIn('Please tell us which record is current and send a termination sheet for the extra one', body)
        self.assertIn('- Rhea Hire, Colorado: ADP #056397 (store 3356-03, hired 2023-10-22) and ADP #096327 '
                      '(store 3356-03, hired 2026-07-01); payroll uses #096327', body)
        self.assertIn('Please confirm whether these are the same person:\n'
                      '- ADP Tori Mclarin (#000921, Texas) and TCP Tori Mclaren (ID 4, store 3910-02)\n', body)
        self.assertNotIn('share the same name', body)  # Sections appear only when they have rows.
        self.assertTrue(body.rstrip().endswith('People hired during these two weeks are not included.\n\nThanks,\nAB'))
        self.assertEqual(list(message.iter_attachments()), [])
        self.assertEqual(message.get_content_type(), 'text/plain')

    def test_email_same_name_section_and_section_order(self):
        employees = [tcp(1, 'Jo', 'Garza', 'AZ', '03759-01'), tcp(2, 'Uma', 'Spell', 'AZ', '03759-01'),
                     tcp(3, 'Ivy', 'Left', 'AZ', '03759-01')]
        rows = [adp('G1', 'Jo', 'Garza', 'FNC', '099962'), adp('G2', 'Jo', 'Garza', 'FNC', '099961'),
                adp('S1', 'Uma', 'Spel', 'FNC', '099963'),
                adp('D1', 'Ivy', 'Dup', 'FNC', '099964', hired='2024-01-01'),
                adp('D1', 'Ivy', 'Dup', 'FNC', '099965', hired='2026-01-01')]
        message, _ = z.build_message(DATES, report_of(employees, [punch(1), punch(2)]), rows)
        body = message.get_content()
        self.assertIn('Please tell us which of these ADP file numbers still work:\n'
                      '- Jo Garza, Arizona, ADP #099961, hired 2025-01-01, 2 active ADP records with this name, '
                      '1 worked in TCP\n'
                      '- Jo Garza, Arizona, ADP #099962, hired 2025-01-01, 2 active ADP records with this name, '
                      '1 worked in TCP\n', body)
        order = [body.index(text) for text in ('TEXAS (Wilder)', 'Also 0 hours and still active in TCP',
                                               'more than one active ADP record', 'share the same name',
                                               'Please confirm whether these are the same person')]
        self.assertEqual(order, sorted(order))

    def test_incomplete_tcp_punches_are_detected(self):
        bad = report()
        bad['datasets']['worksegments']['complete'] = False
        self.assertFalse(z.tcp_sources_complete(bad))
        bad = report()
        bad['errors'] = {'worksegments': 'HTTP 500'}
        self.assertFalse(z.tcp_sources_complete(bad))
        self.assertTrue(z.tcp_sources_complete(report()))


if __name__ == '__main__':
    unittest.main()
