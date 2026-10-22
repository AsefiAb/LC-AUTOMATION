"""Confirmed identities, ADP assignment selection and live-status checks.

Every name, GEID and file number here is invented.
"""
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import adp_api
import adp_identities
import review_adp
from adp_roster import fill_region

ENTRY = {'geid': '1000001', 'company': 'FNC', 'file_number': '099901', 'tcp_name': 'Pat Exampel',
         'adp_name': 'Pat Example', 'evidence_ref': 'Owner confirmation YYYY-MM-DD'}
TEMPLATE = ('!CO CODE,BATCH ID,FILE #,BATCH DESCRIPTION,Name,Rate,Reg Hours,O/T Hours,TIP\r\n'
            'FNC,Wrksh003,99901,,Pat Example,H 15.1500,,,\r\n'
            'FNC,Wrksh003,99910,,Zoe Sample,H 15.1500,,,\r\n').encode()
CONTROLS = ('!Column7,Reg Hours,,Reg Hours,,,,80\r\n'
            '!Column8,O/T Hours,,O/T Hours,,,,80\r\n'
            '!Column9,TIP,,Earnings 3 Code,Earnings 3 Amount,TIP,,80\r\n')


def template(*rows, encoding='utf-8'):
    """A complete native FNC EPI template with the given (file number, name) employee rows."""
    lines = ['!CO CODE,BATCH ID,FILE #,BATCH DESCRIPTION,Name,Rate,Reg Hours,O/T Hours,TIP']
    lines += [f'FNC,Wrksh003,{number},,{name},H 15.1500,,,' for number, name in rows]
    return ('\r\n'.join(lines) + '\r\n' + CONTROLS).encode(encoding)


FULL = template(('99901', 'Pat Example'), ('99910', 'Zoe Sample'))


def registry(*entries):
    return json.dumps({'version': 1, 'identities': list(entries)})


def person(geid, first, last, regular='40.00', overtime='0.00', tips='12.34'):
    return dict(geid=geid, first_name=first, last_name=last, regular_hours=regular, overtime_hours=overtime, tips=tips)


def worker(oid, first, last, number, hired='2026-10-01', primary=True, assignment='A', status='Active',
           company='FNC'):
    return {'associate_oid': oid, 'first_name': first, 'last_name': last, 'preferred_name': '',
            'worker_status': status, 'assignment_status': assignment, 'primary': primary, 'company_code': company,
            'file_number': number, 'job_title': 'TEAM MEMBER', 'location': '', 'hire_date': hired,
            'termination_date': '', 'hourly_rate': '15.15'}


class IdentityTests(unittest.TestCase):
    def test_confirmed_match_resolves_to_the_roster_row(self):
        identities, problems = adp_identities.load(registry(ENTRY))
        self.assertEqual(problems, [])
        overrides, notes = adp_identities.overrides_for(TEMPLATE, 'FNC', 'AZ', identities)
        self.assertEqual(overrides, {'1000001': {'file_number': '99901', 'adp_name': 'Pat Example',
                                                 'tcp_name': 'Pat Exampel'}})
        self.assertEqual(notes, [])
        self.assertEqual(adp_identities.overrides_for(TEMPLATE, 'FND', 'CO', identities), ({}, []))

    def test_confirmed_name_differing_only_in_case_or_spacing_still_applies(self):
        loose = dict(ENTRY, adp_name='  pat   EXAMPLE ')
        overrides, notes = adp_identities.overrides_for(TEMPLATE, 'FNC', 'AZ', [loose])
        # The roster's own spelling is used, because fill_region compares names exactly.
        self.assertEqual(overrides['1000001']['adp_name'], 'Pat Example')
        self.assertEqual(notes, [])

    def test_file_not_on_roster_is_reported_not_guessed(self):
        moved = dict(ENTRY, file_number='099999')
        overrides, notes = adp_identities.overrides_for(TEMPLATE, 'FNC', 'AZ', [moved])
        self.assertEqual(overrides, {})
        self.assertIn('not on the ADP roster', notes[0])
        self.assertIn('not on the last ADP export', notes[0])

    def test_confirmed_file_now_carrying_another_name_is_not_applied(self):
        # A typo (or reused file number) pointing at someone else's ADP row.
        typo = dict(ENTRY, file_number='099910')
        overrides, notes = adp_identities.overrides_for(FULL, 'FNC', 'AZ', [typo])
        self.assertEqual(overrides, {})
        self.assertEqual(len(notes), 1)
        self.assertIn('no longer has the confirmed ADP name', notes[0])
        _, audit = fill_region(None, '3759', FULL, overrides, review_people=[person('1000001', 'Pat', 'Exampel')])
        self.assertFalse(audit['ready'])
        self.assertIn('AZ GEID 1000001: ADP identity missing or ambiguous', audit['issues'])
        row = next(e for e in audit['employees'] if e['file_number'] == '99910')
        self.assertEqual((row['geid'], row['regular_hours'], row['tips']), ('', '', ''))

    def test_confirmed_name_matching_the_alternate_name_adp_writes_in_the_row_applies(self):
        aliased = template(('99901', 'Pat Example(Patty Example)'), ('99910', 'Zoe Sample'))
        for adp_name in ('Pat Example', 'patty  example', 'Pat Example(Patty Example)'):
            with self.subTest(adp_name=adp_name):
                overrides, notes = adp_identities.overrides_for(aliased, 'FNC', 'AZ', [dict(ENTRY, adp_name=adp_name)])
                self.assertEqual(overrides, {'1000001': {'file_number': '99901', 'adp_name': 'Pat Example(Patty Example)',
                                                         'tcp_name': 'Pat Exampel'}})
                self.assertEqual(notes, [])
        _, audit = fill_region(None, '3759', aliased, overrides, review_people=[person('1000001', 'Pat', 'Exampel')])
        row = next(e for e in audit['employees'] if e['file_number'] == '99901')
        self.assertEqual((row['geid'], row['regular_hours'], row['tips']), ('1000001', '40.00', '12.34'))
        # Any other name is still a mismatch, even next to an alias: never a fuzzy match.
        overrides, notes = adp_identities.overrides_for(aliased, 'FNC', 'AZ', [dict(ENTRY, adp_name='Pat Sample')])
        self.assertEqual(overrides, {})
        self.assertIn('no longer has the confirmed ADP name', notes[0])

    def test_invalid_or_duplicate_registry_is_not_used(self):
        for value in ('not json', json.dumps({'version': 2, 'identities': []}), json.dumps([ENTRY]),
                      registry(dict(ENTRY, company='XYZ')),
                      registry(ENTRY, dict(ENTRY, file_number='099902')),
                      registry(ENTRY, dict(ENTRY, geid='1000002')),
                      registry(ENTRY, dict(ENTRY, geid='1000002', file_number='99901')),
                      registry(dict(ENTRY, extra='x')), registry(ENTRY, 5)):
            with self.subTest(value=value[:40]):
                identities, problems = adp_identities.load(value)
                self.assertEqual(identities, [])
                self.assertEqual(problems, [adp_identities.INVALID])
        self.assertEqual(adp_identities.load(''), ([], []))

    def test_numeric_or_non_text_values_are_rejected_without_raising(self):
        for changes in ({'file_number': 99901}, {'geid': 1000001}, {'geid': 1000001, 'file_number': 99901},
                        {'tcp_name': 7}, {'evidence_ref': None}, {'company': ['FNC']},
                        {'file_number': '٩٩٩٠١'}, {'geid': '10 01'}):
            with self.subTest(changes=changes):
                self.assertEqual(adp_identities.load(registry(dict(ENTRY, **changes))), ([], [adp_identities.INVALID]))
        with patch.dict(os.environ, {'PAYROLL_ADP_IDENTITIES': registry(dict(ENTRY, file_number=99901))}):
            self.assertEqual(adp_identities.overrides_for(TEMPLATE, 'FNC', 'AZ'), ({}, [adp_identities.INVALID]))

    def test_numeric_registry_still_delivers_the_review_candidate_with_a_note(self):
        env = {'ADP_AZ_TEMPLATE': base64.b64encode(FULL).decode(), 'ADP_ROSTER_SETTINGS': '{}',
               'PAYROLL_ADP_IDENTITIES': registry(dict(ENTRY, geid=1000001, file_number=99901))}
        dates = {'start_date': '2026-09-22', 'end_date': '2026-10-05'}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, env, clear=True):
            name, audit = review_adp.prepare_candidate('3759', [person('1000001', 'Pat', 'Exampel')], dates,
                                                       Path(folder))
            self.assertEqual(name, 'EPIFNC00.CSV')
            self.assertTrue((Path(folder) / name).exists())
        self.assertIn(adp_identities.INVALID, audit['issues'])
        self.assertEqual([m['geid'] for m in audit['missing']], ['1000001'])


class SettingsIdentityTests(unittest.TestCase):
    SETTINGS = {'period': {}, 'identities': {
        'AZ': {'1000003': {'file_number': '99903', 'adp_name': 'Lee Sample', 'tcp_name': 'Lee Sampel'}},
        'CO': {'1000004': {'file_number': '099904', 'adp_name': 'Kim Test', 'tcp_name': 'Kim Tset'}},
        'XX': {'1000009': {'file_number': '99909', 'adp_name': 'No Region', 'tcp_name': 'No Region'}},
        'TX': {'abc': {'file_number': '99905', 'adp_name': 'Bad Geid', 'tcp_name': 'Bad Geid'},
               '1000005': {'file_number': 99905, 'adp_name': 'Number File', 'tcp_name': 'Number File'},
               '1000006': 'not a mapping',
               '1000007': {'file_number': '', 'adp_name': '', 'tcp_name': ''},
               '1000008': {'file_number': '99908', 'adp_name': 'Lou Example'}}}}

    def test_saved_settings_become_identity_entries_and_malformed_ones_are_skipped(self):
        found = adp_identities.from_settings(self.SETTINGS)
        self.assertEqual(found, [
            {'geid': '1000003', 'company': 'FNC', 'file_number': '99903', 'tcp_name': 'Lee Sampel',
             'adp_name': 'Lee Sample', 'evidence_ref': 'ADP_ROSTER_SETTINGS'},
            {'geid': '1000004', 'company': 'FND', 'file_number': '099904', 'tcp_name': 'Kim Tset',
             'adp_name': 'Kim Test', 'evidence_ref': 'ADP_ROSTER_SETTINGS'}])
        self.assertTrue(all(adp_identities.valid_entry(item) for item in found))
        for bad in (None, 'x', [], {}, {'identities': []}, {'identities': {'AZ': []}}, {'identities': {1: {}}}):
            with self.subTest(bad=bad):
                self.assertEqual(adp_identities.from_settings(bad), [])

    def test_merged_puts_the_secret_first_and_skips_conflicting_saved_matches(self):
        saved = {'identities': {'AZ': {
            '1000001': {'file_number': '99901', 'adp_name': 'Pat Example', 'tcp_name': 'Pat Exampel'},  # repeat
            '1000002': {'file_number': '099901', 'adp_name': 'Pat Example', 'tcp_name': 'Other Person'},  # same file
            '1000003': {'file_number': '99903', 'adp_name': 'Lee Sample', 'tcp_name': 'Lee Sampel'}},
            'CO': {'1000001': {'file_number': '99950', 'adp_name': 'Pat Example', 'tcp_name': 'Pat Exampel'}}}}
        with patch.dict(os.environ, {'PAYROLL_ADP_IDENTITIES': registry(ENTRY)}):
            identities, problems = adp_identities.merged(saved)
        self.assertEqual([(i['geid'], i['file_number'], i['evidence_ref']) for i in identities],
                         [('1000001', '099901', ENTRY['evidence_ref']), ('1000003', '99903', 'ADP_ROSTER_SETTINGS')])
        self.assertEqual(len(problems), 1)
        self.assertIn('2 saved ADP_ROSTER_SETTINGS identity match(es) conflict', problems[0])

    def test_merged_still_uses_saved_matches_when_the_secret_is_invalid_or_absent(self):
        with patch.dict(os.environ, {'PAYROLL_ADP_IDENTITIES': registry(dict(ENTRY, geid=5))}):
            identities, problems = adp_identities.merged(self.SETTINGS)
        self.assertEqual([i['geid'] for i in identities], ['1000003', '1000004'])
        self.assertEqual(problems, [adp_identities.INVALID])
        with patch.dict(os.environ, {'PAYROLL_ADP_IDENTITIES': ''}):
            self.assertEqual(adp_identities.merged(None), ([], []))


class AssignmentAndOrderTests(unittest.TestCase):
    def test_one_current_assignment_per_person_never_merging_different_people(self):
        rows = [worker('P1', 'Rehire', 'Person', '099426', hired='2026-10-19'),
                worker('P1', 'Rehire', 'Person', '099109', hired='2024-10-29', primary=False, assignment='T'),
                worker('P2', 'Two', 'Assign', '098327', hired='2026-07-01'),
                worker('P2', 'Two', 'Assign', '098397', hired='2023-10-22', primary=False),
                worker('P3', 'Emma', 'Example', '099880'),
                worker('P3', 'Emma', 'Example', '', primary=False),
                worker('P4', 'Sam', 'Smith', '099001'),
                worker('P5', 'Sam', 'Smith', '099002')]
        current, older = adp_api.current_workers(rows, 'FNC')
        self.assertEqual(sorted(w['file_number'] for w in current), ['098327', '099001', '099002', '099426', '099880'])
        self.assertEqual([(w['file_number'], w['replaced_by'], w['replaced_hire_date']) for w in older],
                         [('098397', '098327', '2026-07-01')])

    def test_newest_hire_wins_even_when_the_older_assignment_is_primary(self):
        rows = [worker('P2', 'Two', 'Assign', '098397', hired='2023-10-22', primary=True),
                worker('P2', 'Two', 'Assign', '098327', hired='2026-08-03', primary=False)]
        current, older = adp_api.current_workers(rows, 'FNC')
        self.assertEqual([w['file_number'] for w in current], ['098327'])
        self.assertEqual([(w['file_number'], w['replaced_by'], w['replaced_hire_date']) for w in older],
                         [('098397', '098327', '2026-08-03')])
        content, added = adp_api.augment_template(template(('99901', 'Pat Example')), 'FNC', rows)
        self.assertEqual([a['file_number'] for a in added], ['98327'])
        self.assertNotIn(b'98397', content)

    def test_primary_then_higher_file_number_break_ties_on_the_same_or_blank_hire_date(self):
        for hired in ('2026-08-03', ''):
            with self.subTest(hired=hired):
                rows = [worker('P2', 'Two', 'Assign', '098397', hired=hired, primary=False),
                        worker('P2', 'Two', 'Assign', '098327', hired=hired, primary=True)]
                current, older = adp_api.current_workers(rows, 'FNC')
                self.assertEqual([w['file_number'] for w in current], ['098327'])
                self.assertEqual([(w['file_number'], w['replaced_hire_date']) for w in older], [('098397', hired)])
        rows = [worker('P2', 'Two', 'Assign', '98327', primary=False), worker('P2', 'Two', 'Assign', '098397',
                                                                             primary=False)]
        self.assertEqual([w['file_number'] for w in adp_api.current_workers(rows, 'FNC')[0]], ['098397'])

    def test_new_rows_are_placed_by_last_name_then_first_name(self):
        raw = ('!CO CODE,BATCH ID,FILE #,BATCH DESCRIPTION,Name,Rate,Reg Hours,O/T Hours,TIP\r\n'
               'FNC,Wrksh003,10010,,Ann Brown,H 15.1500,1.00,0.00,0.00\r\n'
               'FNC,Wrksh003,10020,,Zack Brown,H 15.1500,,,\r\n'
               'FNC,Wrksh003,10030,,Sara De La Prueba,H 15.1500,,,\r\n'
               'FNC,Wrksh003,10040,,Amy Zulu,H 15.1500,,,\r\n').encode()
        rows = [worker('B1', 'Ann', 'Brown', '010010'), worker('B2', 'Zack', 'Brown', '010020'),
                worker('B3', 'Sara', 'De La Prueba', '010030'), worker('B4', 'Amy', 'Zulu', '010040'),
                worker('N1', 'Mia', 'Brown', '010050'), worker('N2', 'Vic', 'De La Prueba', '010060'),
                worker('N3', 'Al', 'Davis', '010070')]
        content, added = adp_api.augment_template(raw, 'FNC', rows)
        names = [line.split(',')[4] for line in content.decode().split('\r\n') if line.startswith('FNC,')]
        self.assertEqual(names, ['Ann Brown', 'Mia Brown', 'Zack Brown', 'Al Davis', 'Sara De La Prueba',
                                 'Vic De La Prueba', 'Amy Zulu'])
        self.assertIn('FNC,Wrksh003,10010,,Ann Brown,H 15.1500,1.00,0.00,0.00', content.decode())
        self.assertEqual(len(added), 3)

    def test_name_the_template_encoding_cannot_hold_is_reported_and_template_kept(self):
        raw = template(('99930', 'José Ejemplo'), encoding='cp1252')
        rows = [worker('E1', 'José', 'Ejemplo', '099930'), worker('E2', 'Michał', 'Przykład', '099931')]
        with self.assertRaises(adp_api.ADPError):
            adp_api.augment_template(raw, 'FNC', rows)
        original = bytes(raw)
        with patch('adp_api.missing_secrets', return_value=[]), \
                patch('adp_api.workers_once', return_value=(rows, None)):
            merged, notes, added = review_adp.live_roster(raw, 'FNC', 'AZ')
        self.assertEqual(merged, original)
        self.assertEqual(len(notes), 1)
        self.assertIn('could not be merged', notes[0])
        self.assertEqual(added, [])
        # A name the encoding can hold is still added, in that encoding.
        rows[1] = worker('E2', 'Renée', 'Przyklad', '099931')
        content, added = adp_api.augment_template(raw, 'FNC', rows)
        self.assertIn('FNC,Wrksh003,99931,,Renée Przyklad,H 15.1500,,,'.encode('cp1252'), content)

    def test_unexpected_merge_failure_keeps_the_last_export(self):
        with patch('adp_api.missing_secrets', return_value=[]), \
                patch('adp_api.workers_once', return_value=([{'company_code': 'FNC'}], None)):
            merged, notes, added = review_adp.live_roster(FULL, 'FNC', 'AZ')
        self.assertEqual((merged, added), (FULL, []))
        self.assertIn('could not be merged (KeyError)', notes[0])


class Response:
    def __init__(self, status, body, content_type='application/json'):
        self.status_code, self.body, self.headers = status, body, {'content-type': content_type}

    def json(self):
        if not isinstance(self.body, str):
            return self.body
        return json.loads(self.body)  # Raises ValueError for an HTML page, like requests.


class Session:
    def __init__(self, token, pages=()):
        self.token, self.pages, self.posts, self.gets = token, list(pages), 0, 0

    def post(self, *args, **kwargs):
        self.posts += 1
        return self.token

    def get(self, *args, **kwargs):
        self.gets += 1
        return self.pages.pop(0) if self.pages else Response(204, {})


SECRETS = {'ADP_CLIENT_ID': 'id', 'ADP_CLIENT_SECRET': 'secret',
           'ADP_CERTIFICATE': '-----BEGIN CERTIFICATE-----\\nAAAA\\n-----END CERTIFICATE-----',
           'ADP_PRIVATE_KEY': '-----BEGIN PRIVATE KEY-----\\nAAAA\\n-----END PRIVATE KEY-----'}
TOKEN = Response(200, {'access_token': 'token'})


def adp_worker(oid, legal):
    return {'associateOID': oid, 'person': {'legalName': legal}, 'workerStatus': {'statusCode': {'codeValue': 'Active'}},
            'workAssignments': [{'payrollGroupCode': 'FND', 'payrollFileNumber': '099940',
                                 'assignmentStatus': {'statusCode': {'codeValue': 'A'}}}]}


class FetchTests(unittest.TestCase):
    def setUp(self):
        adp_api._CACHE.clear()

    def tearDown(self):
        adp_api._CACHE.clear()

    def read_twice(self, session):
        with patch.dict(os.environ, SECRETS), patch('adp_api.requests.Session', return_value=session):
            first, second = adp_api.workers_once(), adp_api.workers_once()
        self.assertEqual(first, second)
        self.assertEqual(session.posts, 1)
        return first

    def test_maintenance_page_is_cached_once_and_the_review_falls_back_to_the_last_export(self):
        session = Session(Response(200, '<html>maintenance</html>', 'text/html'))
        rows, problem = self.read_twice(session)
        self.assertIsNone(rows)
        self.assertEqual(problem, 'ADP token request returned HTTP 200 without JSON')
        with patch.dict(os.environ, SECRETS):
            raw, notes, added = review_adp.live_roster(b'last export', 'FND', 'CO')
        self.assertEqual((raw, added), (b'last export', []))
        self.assertEqual(notes, ['CO: live ADP roster unavailable (ADP token request returned HTTP 200 without JSON); '
                                 'CSV uses the last supplied export only.'])
        self.assertEqual(session.posts, 1)

    def test_malformed_worker_payloads_become_one_cached_problem(self):
        for page, expected in ((Response(200, {'workers': [adp_worker('W1', None)]}), 'unexpected shape'),
                               (Response(200, {'workers': {'W1': {}}}), 'not a list'),
                               (Response(200, {'workers': ['W1']}), 'not a list'),
                               (Response(200, ['W1']), 'unexpected JSON'),
                               (Response(200, '<html></html>', 'text/html'), 'workers request returned HTTP 200 without JSON')):
            with self.subTest(expected=expected):
                adp_api._CACHE.clear()
                rows, problem = self.read_twice(Session(TOKEN, [page]))
                self.assertIsNone(rows)
                self.assertIn(expected, problem)

    def test_token_failures_are_reported_without_the_body(self):
        for token, expected in ((Response(503, '<html>down</html>', 'text/html'), 'ADP token request returned HTTP 503'),
                                (Response(401, {'error': 'invalid_client<x>'}), 'HTTP 401 invalid_clientx'),
                                (Response(200, {'token_type': 'Bearer'}), 'without an access token')):
            with self.subTest(expected=expected):
                adp_api._CACHE.clear()
                rows, problem = self.read_twice(Session(token))
                self.assertIsNone(rows)
                self.assertIn(expected, problem)

    def test_a_good_read_is_cached(self):
        session = Session(TOKEN, [Response(200, {'workers': [adp_worker('W1', {'givenName': 'Kim',
                                                                                'familyName1': 'Test'})]})])
        rows, problem = self.read_twice(session)
        self.assertIsNone(problem)
        self.assertEqual([(r['first_name'], r['file_number']) for r in rows], [('Kim', '099940')])
        self.assertEqual(session.gets, 2)

    def test_any_other_failure_is_cached_by_type_name_only(self):
        with patch('adp_api.fetch_workers', side_effect=ValueError('https://example.invalid/?token=abc')) as fetch:
            first, second = adp_api.workers_once(), adp_api.workers_once()
        self.assertEqual(first, (None, 'ADP roster read failed (ValueError)'))
        self.assertEqual(second, first)
        self.assertEqual(fetch.call_count, 1)


LIVE = [worker('A1', 'Two', 'Assign', '098397', hired='2023-10-22', primary=True),
        worker('A1', 'Two', 'Assign', '098327', hired='2026-07-01', primary=False),
        worker('A2', 'Gone', 'Person', '099920', status='Terminated', assignment='T'),
        worker('A3', 'Pat', 'Example', '099901'),
        worker('A4', 'Zoe', 'Sample', '099910')]
OLD = dict(ENTRY, geid='1000010', file_number='098397', tcp_name='Two Asign', adp_name='Two Assign')
GONE = dict(ENTRY, geid='1000020', file_number='099920', tcp_name='Gone Person', adp_name='Gone Person')


class LiveStatusTests(unittest.TestCase):
    def overrides(self, raw, entries, settings=None, workers=(LIVE, None), secrets=()):
        env = {'PAYROLL_ADP_IDENTITIES': registry(*entries)}
        with patch.dict(os.environ, env), patch('adp_api.missing_secrets', return_value=list(secrets)), \
                patch('adp_api.workers_once', return_value=workers):
            return review_adp.identity_overrides(raw, 'FNC', 'AZ', {} if settings is None else settings)

    def test_current_assignment_is_kept(self):
        overrides, notes = self.overrides(FULL, [ENTRY])
        self.assertEqual(overrides, {'1000001': {'file_number': '99901', 'adp_name': 'Pat Example',
                                                 'tcp_name': 'Pat Exampel'}})
        self.assertEqual(notes, [])

    def test_older_assignment_moves_to_the_newest_file_when_it_is_on_the_roster(self):
        raw = template(('98327', 'Two Assign'), ('98397', 'Two Assign'), ('99901', 'Pat Example'))
        overrides, notes = self.overrides(raw, [OLD])
        self.assertEqual(overrides, {'1000010': {'file_number': '98327', 'adp_name': 'Two Assign',
                                                 'tcp_name': 'Two Asign'}})
        self.assertEqual(len(notes), 1)
        self.assertIn('older assignment', notes[0])
        self.assertIn('98327', notes[0])
        _, audit = fill_region(None, '3759', raw, overrides, review_people=[person('1000010', 'Two', 'Asign')])
        row = next(e for e in audit['employees'] if e['geid'] == '1000010')
        self.assertEqual((row['file_number'], row['regular_hours']), ('98327', '40.00'))

    def test_older_assignment_is_blocked_when_the_newest_file_is_not_on_the_roster(self):
        raw = template(('98397', 'Two Assign'), ('99901', 'Pat Example'))
        overrides, notes = self.overrides(raw, [dict(OLD, tcp_name='Two Assign')])
        self.assertEqual(overrides['1000010'], review_adp.BLOCKED)
        self.assertIn('replaced by newer ADP file 098327', notes[0])
        _, audit = fill_region(None, '3759', raw, overrides, review_people=[person('1000010', 'Two', 'Assign')])
        self.assertEqual([m['geid'] for m in audit['missing']], ['1000010'])
        self.assertTrue(all(e['geid'] == '' for e in audit['employees']))  # Never exact-name matched to 98397.

    def test_terminated_assignment_is_blocked_for_confirmed_and_saved_matches(self):
        raw = template(('99901', 'Pat Example'), ('99920', 'Gone Person'))
        saved = {'identities': {'AZ': {'1000030': {'file_number': '99920', 'adp_name': 'Gone Person',
                                                   'tcp_name': 'Gone Persson'}}}}
        overrides, notes = self.overrides(raw, [GONE], saved)
        self.assertEqual(overrides, {'1000020': review_adp.BLOCKED, '1000030': review_adp.BLOCKED})
        self.assertEqual(len(notes), 2)
        self.assertTrue(all('terminated or not active in ADP' in n for n in notes))
        people = [person('1000020', 'Gone', 'Person'), person('1000030', 'Gone', 'Persson')]
        _, audit = fill_region(None, '3759', raw, overrides, review_people=people)
        self.assertEqual(sorted(m['geid'] for m in audit['missing']), ['1000020', '1000030'])
        self.assertTrue(all(e['geid'] == '' for e in audit['employees']))

    def test_malformed_saved_match_is_blocked_not_name_matched(self):
        saved = {'identities': {'AZ': {'1000050': 'Zoe Sample', '1000060': dict(review_adp.BLOCKED)}}}
        overrides, notes = self.overrides(FULL, [ENTRY], saved)
        self.assertEqual((overrides['1000050'], overrides['1000060']), (review_adp.BLOCKED, review_adp.BLOCKED))
        self.assertEqual(sum('no usable ADP file number' in n for n in notes), 2)
        self.assertEqual(overrides['1000001']['file_number'], '99901')
        _, audit = fill_region(None, '3759', FULL, overrides, review_people=[person('1000050', 'Zoe', 'Sample')])
        self.assertEqual([m['geid'] for m in audit['missing']], ['1000050'])

    def test_review_candidate_uses_the_newest_file_added_from_the_live_roster(self):
        raw = template(('98397', 'Two Assign'), ('99901', 'Pat Example'), ('99910', 'Zoe Sample'))
        env = {'ADP_AZ_TEMPLATE': base64.b64encode(raw).decode(), 'ADP_ROSTER_SETTINGS': '{}',
               'PAYROLL_ADP_IDENTITIES': registry(OLD, GONE)}
        dates = {'start_date': '2026-09-22', 'end_date': '2026-10-05'}
        people = [person('1000010', 'Two', 'Asign'), person('1000020', 'Gone', 'Person', tips='0.00')]
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, env, clear=True), \
                patch('adp_api.missing_secrets', return_value=[]), \
                patch('adp_api.workers_once', return_value=(LIVE, None)):
            name, audit = review_adp.prepare_candidate('3759', people, dates, Path(folder))
        self.assertEqual(name, 'EPIFNC00.CSV')
        self.assertEqual([a['file_number'] for a in audit['adp_added']], ['98327'])
        row = next(e for e in audit['employees'] if e['geid'] == '1000010')
        self.assertEqual(row['file_number'], '98327')
        self.assertEqual([m['geid'] for m in audit['missing']], ['1000020'])
        self.assertTrue(any('confirmed ADP file 099920 is not on the ADP roster' in i for i in audit['issues']))
        self.assertTrue(any('GEID 1000010: matched ADP file 98397 is an older assignment' in i
                            for i in audit['issues']))

    def test_live_status_unavailable_keeps_matches_with_a_note(self):
        overrides, notes = self.overrides(FULL, [ENTRY], workers=(None, 'ADP roster read failed (ValueError)'))
        self.assertEqual(overrides['1000001']['file_number'], '99901')
        self.assertEqual(notes, ['AZ: matched ADP files could not be checked against live ADP status '
                                 '(ADP roster read failed (ValueError)).'])
        overrides, notes = self.overrides(FULL, [ENTRY], workers=([worker('Z1', 'Kim', 'Test', '000001',
                                                                         company='ZKT')], None))
        self.assertEqual(overrides['1000001']['file_number'], '99901')
        self.assertIn('lists no active FNC workers', notes[0])
        overrides, notes = self.overrides(FULL, [ENTRY], secrets=['ADP_CLIENT_ID'])
        self.assertEqual((overrides['1000001']['file_number'], notes), ('99901', []))

    def test_any_failure_returns_the_saved_matches_with_a_note(self):
        saved = {'identities': {'AZ': {'1000040': {'file_number': '99910', 'adp_name': 'Zoe Sample',
                                                   'tcp_name': 'Zoe Sampel'}}}}
        with patch('adp_identities.overrides_for', side_effect=RuntimeError('boom')):
            overrides, notes = self.overrides(FULL, [ENTRY], saved)
        self.assertEqual(overrides, saved['identities']['AZ'])
        self.assertEqual(notes, ['AZ: confirmed ADP identity list could not be applied (RuntimeError); '
                                 'confirmed name matches were not used.'])
        for settings in ({'identities': []}, {'identities': {'AZ': 'x'}}, []):
            with self.subTest(settings=settings):
                overrides, notes = self.overrides(FULL, [ENTRY], settings)
                self.assertEqual(list(overrides), ['1000001'])


if __name__ == '__main__':
    unittest.main()
