"""payroll_run's 0-hours step, its scheduled catch-up, and the optional ADP notes in final payroll.

Every name, GEID and ADP file number here is invented for the tests.
"""
import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest import mock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'sales automation'))

import adp_api
import adp_identities
import payroll_run
from adp_roster import build_imports
import daily_report

DATES = {'start_date': '2026-10-06', 'end_date': '2026-10-19'}
RECEIPT = '2026-10-19-zero-hours'
ADP_ENV = {'ADP_CLIENT_ID': 'test-id', 'ADP_CLIENT_SECRET': 'test-secret',
           'ADP_CERTIFICATE': 'test-cert', 'ADP_PRIVATE_KEY': 'test-key'}
TCP_ENV = {'TCP_CLIENT_ID': 'test', 'TCP_CLIENT_SECRET': 'test', 'TCP_API_KEY': 'test'}
DATA = {'AZ': {'zero_hours': [{'name': 'Quill Ambersong'}], 'check_names': [], 'tcp_only': []},
        'CO': {'zero_hours': [], 'check_names': [], 'tcp_only': []},
        'TX': {'zero_hours': [], 'check_names': [], 'tcp_only': [{'name': 'Rowan Featherby'}]}}
SETTINGS = {'period': DATES, 'identities': {'AZ': {'9001': {
    'file_number': '099101', 'adp_name': 'Quill Ambersong-Vale', 'tcp_name': 'Quill Ambersong'}}}}
SECRET_ENTRY = {'geid': '9003', 'company': 'ZKT', 'file_number': '099301', 'tcp_name': 'Rowan Featherbee',
                'adp_name': 'Rowan Featherby', 'evidence_ref': 'Owner confirmation (test)'}


def report():
    """A complete invented TCP read: every dataset complete and no errors."""
    people = [{'employeeId': 9001, 'firstName': 'Quill', 'lastName': 'Ambersong', 'location': '03759-00001'},
              {'employeeId': 9003, 'firstName': 'Rowan', 'lastName': 'Featherbee', 'location': '03910-00001'}]
    punches = [{'employeeId': 9001, 'timeIn': '2026-10-08T10:00:00'}]
    datasets = {name: {'complete': True, 'count': 0, 'records': []}
                for name in ('locations', 'jobcodes', 'employeejobcodes', 'hours')}
    datasets['employees'] = {'complete': True, 'count': len(people), 'records': people}
    datasets['worksegments'] = {'complete': True, 'count': len(punches), 'records': punches}
    return {'period': {'startDate': DATES['start_date'], 'stopDate': DATES['end_date']},
            'errors': {}, 'generated_at': 'test', 'datasets': datasets}


def worker(first, last, company, number, hired='2025-01-01', rate='15.00', oid=None):
    return {'associate_oid': oid or 'OID-' + number, 'first_name': first, 'last_name': last, 'preferred_name': '',
            'worker_status': 'Active', 'assignment_status': 'A', 'primary': True, 'company_code': company,
            'file_number': number, 'job_title': 'TEAM MEMBER', 'location': '', 'hire_date': hired,
            'termination_date': '', 'hourly_rate': rate}


def workers():
    return [worker('Quill', 'Ambersong-Vale', 'FNC', '099101'), worker('Rowan', 'Featherby', 'ZKT', '099301')]


def zero_message():
    message = EmailMessage()
    message['From'] = 'sender@example.invalid'
    message['To'] = 'district@example.invalid'
    message['Cc'] = 'copy@example.invalid'
    message['Subject'] = 'Employees with 0 hours - 10.6.26-10.19.26'
    message['Message-ID'] = '<zero-hours-test@example.invalid>'
    message.set_content('Invented test body.')
    return message


def review_payroll():
    """Payroll with an open issue, so main() takes the review path (no final ADP files)."""
    return {'period': DATES, 'verified': False, 'issues': ['Missing calculated shift'],
            'roster': [{'geid': '9001', 'first_name': 'Quill', 'last_name': 'Ambersong', 'roles': ['crew']}],
            'employees': [{'store': '3759-01', 'employee_id': '9001', 'roles': ['crew'], 'regular_hours': '10.00',
                           'overtime_hours': '0.00', 'total_hours': '10.00'}],
            'warnings': []}


def tips():
    return {'period': DATES, 'verified': True, 'issues': [],
            'stores': [{'store': s, 'total_tips': '12.34' if s == '3759-01' else '0.00'} for s in payroll_run.STORES]}


class Ledger:
    """In-memory stand-in for daily_report.DeliveryLedger (no GitHub API)."""
    receipts = {}

    def __init__(self, day, namespace='sales'):
        self.key = (namespace, day)
        self.writes = []

    def read(self):
        return Ledger.receipts.get(self.key)

    def write(self, value):
        Ledger.receipts[self.key] = dict(value)


class StepCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.output = self.root / 'reports' / '2026-10-06_2026-10-19'
        self.output.mkdir(parents=True)
        self.summary = self.root / 'step-summary.md'

    def env(self, event='schedule', **extra):
        values = {**ADP_ENV, 'CAAZ_GMAIL_APP_PASS': 'test-not-real', 'GITHUB_EVENT_NAME': event,
                  'PAYROLL_ADP_IDENTITIES': '', 'GITHUB_STEP_SUMMARY': str(self.summary)}
        values.update(extra)
        return patch.dict(os.environ, {k: v for k, v in values.items() if v is not None}, clear=True)

    def saved(self):
        return json.loads((self.output / 'zero-hours' / 'zero-hours-result.json').read_text(encoding='utf-8'))

    def summary_text(self):
        return self.summary.read_text(encoding='utf-8') if self.summary.exists() else ''

    def step(self, mode, *, settings=None, data=DATA, rows=None, problem=None, build=None, report_value=None):
        """Run zero_hours_step with ADP, the message builder and delivery replaced; returns (result, mocks)."""
        message = zero_message()
        with patch('adp_api.workers_once', return_value=(None, problem) if problem else (rows or workers(), None)), \
                patch('zero_hours_notice.build_message', side_effect=build,
                      return_value=(message, data)) as builder, \
                patch('daily_report.DeliveryLedger') as ledger, \
                patch('daily_report.deliver', return_value='sent') as send, \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            result = payroll_run.zero_hours_step(mode, DATES, report() if report_value is None else report_value,
                                                 self.output, settings)
        return result, {'builder': builder, 'ledger': ledger, 'send': send, 'message': message,
                        'printed': printed.getvalue()}


class ZeroHoursStepTests(StepCase):
    def test_scheduled_deliver_sends_once_under_its_own_receipt(self):
        with self.env('schedule'):
            result, m = self.step('deliver')
        self.assertEqual(result['status'], 'sent')
        m['ledger'].assert_called_once_with(RECEIPT, namespace='payroll')
        m['send'].assert_called_once_with(m['message'], m['ledger'].return_value, '2026-10-19')
        saved = self.saved()
        self.assertEqual(saved['status'], 'sent')
        self.assertEqual(saved['counts']['AZ'], {'zero_hours': 1, 'check_names': 0, 'tcp_only': 0})
        self.assertTrue((self.output / 'zero-hours' / 'zero-hours.eml').exists())
        self.assertEqual(self.summary_text(), '')  # A sent email needs no job-summary note.

    def test_manual_runs_and_preflight_and_preview_only_preview(self):
        for mode, event, password in (('deliver', 'workflow_dispatch', 'test-not-real'), ('preflight', 'schedule', 'x'),
                                      ('preview', 'schedule', 'x'), ('deliver', 'schedule', None)):
            with self.subTest(mode=mode, event=event, password=bool(password)), \
                    self.env(event, CAAZ_GMAIL_APP_PASS=password):
                result, m = self.step(mode)
                self.assertEqual(result['status'], 'preview')
                m['send'].assert_not_called()
                m['ledger'].assert_not_called()
                self.assertTrue((self.output / 'zero-hours' / 'zero-hours.eml').exists())
                self.assertEqual(self.saved()['status'], 'preview')
                noted = '0-hours termination email NOT sent: preview' in self.summary_text()
                self.assertEqual(noted, (mode, event) == ('deliver', 'schedule'))

    def test_missing_sources_skip_without_building_or_sending(self):
        incomplete = report()
        incomplete['datasets']['worksegments']['complete'] = False
        cases = [('ADP roster unavailable', {'problem': 'ADP token request returned HTTP 503'}, {}),
                 ('TCP punches are unavailable or incomplete', {'report_value': incomplete}, {}),
                 ('ADP API secrets are missing', {}, {'ADP_PRIVATE_KEY': None})]
        for reason, options, env in cases:
            with self.subTest(reason=reason), self.env('schedule', **env):
                result, m = self.step('deliver', **options)
                self.assertEqual(result['status'], 'skipped')
                self.assertIn(reason, result['reason'])
                m['builder'].assert_not_called()
                m['send'].assert_not_called()
                self.assertIn('0-hours termination email NOT sent: skipped (' + reason, self.summary_text())
        with self.env('schedule'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(payroll_run.zero_hours_step('deliver', DATES, None, self.output)['status'], 'skipped')

    def test_saved_settings_identities_reach_the_email_and_notes_are_saved_not_printed(self):
        def build(dates, tcp, adp, identities=(), notes=None):
            notes.append('AZ: please confirm Quill Ambersong and Quill Ambersong-Vale (ADP #099101)')
            return zero_message(), DATA
        secret = json.dumps({'version': 1, 'identities': [SECRET_ENTRY]})
        with self.env('workflow_dispatch', PAYROLL_ADP_IDENTITIES=secret):
            result, m = self.step('preview', settings=SETTINGS, build=build)
        self.assertEqual(result['status'], 'preview')
        args, kwargs = m['builder'].call_args
        identities = args[3]
        self.assertEqual([(i['geid'], i['company'], i['file_number']) for i in identities],
                         [('9003', 'ZKT', '099301'), ('9001', 'FNC', '099101')])  # Secret first, then settings.
        self.assertEqual(identities[1]['evidence_ref'], 'ADP_ROSTER_SETTINGS')
        self.assertIsInstance(kwargs['notes'], list)
        self.assertEqual(self.saved()['notes'],
                         ['AZ: please confirm Quill Ambersong and Quill Ambersong-Vale (ADP #099101)'])
        self.assertIn('Zero-hours notes: 1', m['printed'])
        for private in ('Quill', 'Ambersong', '099101', '9001'):
            self.assertNotIn(private, m['printed'])

    def test_invalid_identity_secret_is_noted_and_saved_matches_still_apply(self):
        with self.env('workflow_dispatch', PAYROLL_ADP_IDENTITIES='{"version": 2, "identities": []}'):
            result, m = self.step('preview', settings=SETTINGS)
        self.assertEqual(result['status'], 'preview')
        self.assertEqual([i['geid'] for i in m['builder'].call_args.args[3]], ['9001'])
        self.assertEqual(self.saved()['notes'], [adp_identities.INVALID])
        self.assertIn('Zero-hours notes: 1', m['printed'])


class ContainmentTests(StepCase):
    def test_builder_or_identity_failures_are_recorded_and_never_raised(self):
        cases = [('RuntimeError', {'build': RuntimeError('boom for Quill Ambersong')}, None),
                 ('AttributeError', {}, 'adp_identities.merged'),
                 ('AttributeError', {}, 'adp_identities.load')]
        for kind, options, target in cases:
            with self.subTest(kind=kind, target=target), self.env('schedule'), contextlib.ExitStack() as stack:
                if target:
                    stack.enter_context(patch(target, side_effect=AttributeError('x')))
                result, m = self.step('deliver', **options)
                self.assertEqual((result['status'], result['reason']), ('failed', kind))  # No message: not a curated text.
                self.assertEqual(self.saved()['status'], 'failed')
                m['send'].assert_not_called()
                self.assertNotIn('Quill', m['printed'])
                self.assertIn('0-hours termination email NOT sent: failed (' + kind + ')', self.summary_text())

    def test_delivery_and_adp_errors_keep_their_message(self):
        with self.env('schedule'), patch('daily_report.DeliveryLedger'), \
                patch('daily_report.deliver', side_effect=daily_report.ReportError('Delivery ledger request failed (HTTP 502).')), \
                patch('adp_api.workers_once', return_value=(workers(), None)), \
                patch('zero_hours_notice.build_message', return_value=(zero_message(), DATA)), \
                contextlib.redirect_stdout(io.StringIO()):
            result = payroll_run.zero_hours_step('deliver', DATES, report(), self.output)
        self.assertEqual(result['reason'], 'ReportError: Delivery ledger request failed (HTTP 502).')
        self.assertEqual(self.saved()['reason'], result['reason'])
        with self.env('schedule'):
            result, _ = self.step('deliver', build=adp_api.ADPError('ADP workers request returned HTTP 500'))
        self.assertEqual(result['reason'], 'ADPError: ADP workers request returned HTTP 500')

    def test_unwritable_output_or_summary_never_raises(self):
        (self.output / 'zero-hours').write_text('not a folder', encoding='utf-8')
        with self.env('schedule', GITHUB_STEP_SUMMARY=str(self.root)):  # A folder: the summary cannot be opened.
            result, m = self.step('deliver')
        self.assertEqual(result['status'], 'failed')
        m['send'].assert_not_called()
        self.assertIn('Zero-hours result could not be saved', m['printed'])


class DedupeTests(StepCase):
    """The real daily_report.deliver decides; SMTP and the GitHub ledger are replaced."""
    def setUp(self):
        super().setUp()
        Ledger.receipts = {}

    def real_step(self):
        with patch('daily_report.DeliveryLedger', Ledger), patch('daily_report.smtplib.SMTP_SSL') as smtp, \
                patch('adp_api.workers_once', return_value=(workers(), None)), \
                patch('zero_hours_notice.build_message', return_value=(zero_message(), DATA)), \
                contextlib.redirect_stdout(io.StringIO()):
            smtp.return_value.__enter__.return_value.send_message.return_value = {}
            return payroll_run.zero_hours_step('deliver', DATES, report(), self.output), smtp

    def test_sends_once_then_reports_already_sent(self):
        with self.env('schedule'):
            first, smtp = self.real_step()
            self.assertEqual(first['status'], 'sent')
            self.assertEqual(smtp.return_value.__enter__.return_value.send_message.call_count, 1)
            self.assertEqual(Ledger.receipts[('payroll', RECEIPT)]['status'], 'sent')
            second, smtp = self.real_step()
        self.assertEqual(second['status'], 'already sent')
        smtp.assert_not_called()
        self.assertEqual(self.summary_text(), '')

    def test_sent_receipt_never_reaches_smtp(self):
        Ledger.receipts[('payroll', RECEIPT)] = {'status': 'sent'}
        with self.env('schedule'):
            result, smtp = self.real_step()
        self.assertEqual(result['status'], 'already sent')
        smtp.assert_not_called()
        self.assertEqual(Ledger.receipts[('payroll', RECEIPT)], {'status': 'sent'})

    def test_uncertain_receipt_fails_with_its_gmail_check_reason(self):
        Ledger.receipts[('payroll', RECEIPT)] = {'status': 'sending'}
        with self.env('schedule'):
            result, smtp = self.real_step()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Check Gmail Sent', result['reason'])
        self.assertIn('Check Gmail Sent', self.saved()['reason'])
        smtp.assert_not_called()
        self.assertEqual(Ledger.receipts[('payroll', RECEIPT)], {'status': 'sending'})
        self.assertIn('NOT sent: failed (ReportError: A prior send has an uncertain outcome', self.summary_text())


class MainCase(StepCase):
    def main_env(self, mode, event='schedule', **extra):
        settings = dict(SETTINGS)
        return self.env(event, ADP_ROSTER_SETTINGS=json.dumps(settings), **TCP_ENV, **extra), \
            ['payroll_run', '--mode', mode]

    def patches(self, stack, argv, *, sent=False, build=None):
        """main() with every source replaced by invented data; returns the mocks."""
        message = zero_message()
        stack.enter_context(patch('sys.argv', argv))
        stack.enter_context(patch.object(payroll_run, 'FOLDER', self.root))
        stack.enter_context(patch.object(payroll_run, 'period_for', return_value=DATES))
        m = {'message': message,
             'fetch': stack.enter_context(patch.object(payroll_run, 'fetch_tcp', return_value=report())),
             'normalize': stack.enter_context(patch.object(payroll_run, 'normalize', return_value=review_payroll())),
             'ledgers': stack.enter_context(patch.object(payroll_run, 'delivery_ledgers')),
             'final_send': stack.enter_context(patch.object(payroll_run, 'send_regions')),
             'prepare_review': stack.enter_context(patch('review_package.prepare_review', return_value=[])),
             'review_send': stack.enter_context(patch('review_package.send_review',
                                                      return_value={'TX': 'sent', 'AZ': 'sent', 'CO': 'sent'})),
             'builder': stack.enter_context(patch('zero_hours_notice.build_message', side_effect=build,
                                                  return_value=(message, DATA))),
             'zero_ledger': stack.enter_context(patch('daily_report.DeliveryLedger')),
             'send': stack.enter_context(patch('daily_report.deliver', return_value='sent'))}
        stack.enter_context(patch.object(payroll_run, 'write_issues'))
        stack.enter_context(patch.object(payroll_run.gateway, 'missing_secrets', return_value=[]))
        stack.enter_context(patch.object(payroll_run.gateway, 'fetch', return_value=tips()))
        stack.enter_context(patch('adp_api.workers_once', return_value=(workers(), None)))
        m['ledgers'].return_value = {code: mock.Mock() for code in ('TX', 'AZ', 'CO')}
        for ledger in m['ledgers'].return_value.values():
            ledger.read.return_value = {'status': 'sent'} if sent else None
        m['zero_ledger'].return_value.read.return_value = None
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        return m


class MainStepTests(MainCase):
    def test_step_runs_in_every_mode_with_the_saved_identities(self):
        for mode in ('preflight', 'preview', 'deliver'):
            env, argv = self.main_env(mode)
            with self.subTest(mode=mode), env, contextlib.ExitStack() as stack:
                m = self.patches(stack, argv)
                self.assertEqual(payroll_run.main(), 2)
                self.assertTrue((self.output / 'readiness.json').exists())
                self.assertTrue((self.output / 'zero-hours' / 'zero-hours.eml').exists())
                saved = self.saved()
                self.assertEqual(saved['status'], 'sent' if mode == 'deliver' else 'preview')
                m['builder'].assert_called_once()
                self.assertEqual([i['geid'] for i in m['builder'].call_args.args[3]], ['9001'])
                if mode == 'deliver':
                    m['send'].assert_called_once_with(m['message'], m['zero_ledger'].return_value, '2026-10-19')
                    m['review_send'].assert_called_once()
                else:
                    m['send'].assert_not_called()
                m['final_send'].assert_not_called()

    def test_step_failures_never_change_the_payroll_outcome(self):
        failures = [('build', RuntimeError('boom')), ('merged', AttributeError('x'))]
        for mode in ('preflight', 'deliver'):
            for name, error in failures:
                env, argv = self.main_env(mode)
                with self.subTest(mode=mode, failure=name), env, contextlib.ExitStack() as stack:
                    m = self.patches(stack, argv, build=error if name == 'build' else None)
                    if name == 'merged':
                        stack.enter_context(patch('adp_identities.merged', side_effect=error))
                    self.assertEqual(payroll_run.main(), 2)
                    self.assertEqual(self.saved()['status'], 'failed')
                    self.assertFalse(json.loads((self.output / 'readiness.json').read_text())['ready'])
                    m['send'].assert_not_called()
                    if mode == 'deliver':
                        m['review_send'].assert_called_once()
                        self.assertTrue((self.output / 'review-delivery-results.json').exists())
                    else:
                        m['review_send'].assert_not_called()


class AlreadySentCatchUpTests(MainCase):
    """main() in deliver mode after all regional payroll emails were sent."""
    def run_main(self, event='schedule', receipt=None, step=None, ledger_error=None, fetch_error=None):
        env, argv = self.main_env('deliver', event)
        with env, contextlib.ExitStack() as stack:
            m = self.patches(stack, argv, sent=True)
            m['zero_ledger'].return_value.read.return_value = receipt
            if ledger_error:
                m['zero_ledger'].side_effect = ledger_error
            if fetch_error:
                m['fetch'].side_effect = fetch_error
            if step is not False:
                m['step'] = stack.enter_context(patch.object(payroll_run, 'zero_hours_step', side_effect=step,
                                                             return_value={'status': 'sent'}))
            code = payroll_run.main()
        return code, m

    def test_scheduled_slot_without_a_receipt_tries_the_email_once(self):
        code, m = self.run_main()
        self.assertEqual(code, 2)
        m['zero_ledger'].assert_called_once_with(RECEIPT, namespace='payroll')
        m['fetch'].assert_called_once_with(DATES)
        m['step'].assert_called_once_with('deliver', DATES, m['fetch'].return_value, self.output, SETTINGS)
        m['normalize'].assert_not_called()
        m['final_send'].assert_not_called()
        m['review_send'].assert_not_called()

    def test_a_receipt_in_any_status_blocks_the_catch_up(self):
        for receipt in ({'status': 'sent'}, {'status': 'sending'}, {}):
            with self.subTest(receipt=receipt):
                self.summary.unlink(missing_ok=True)
                code, m = self.run_main(receipt=receipt)
                self.assertEqual(code, 2)
                m['step'].assert_not_called()
                m['fetch'].assert_not_called()
                noted = 'check Gmail Sent' in self.summary_text()
                self.assertEqual(noted, receipt.get('status') != 'sent')

    def test_manual_runs_never_catch_up(self):
        code, m = self.run_main(event='workflow_dispatch')
        self.assertEqual(code, 2)
        m['zero_ledger'].assert_not_called()
        m['fetch'].assert_not_called()
        m['step'].assert_not_called()

    def test_catch_up_problems_never_raise(self):
        code, m = self.run_main(ledger_error=daily_report.ReportError('Delivery ledger request failed (HTTP 502).'))
        self.assertEqual(code, 2)
        m['step'].assert_not_called()
        self.assertIn('catch-up could not check its receipt (ReportError)', self.summary_text())
        code, m = self.run_main(fetch_error=RuntimeError('TCP down'))
        self.assertEqual(code, 2)
        m['step'].assert_called_once_with('deliver', DATES, None, self.output, SETTINGS)  # Recorded as skipped.
        code, m = self.run_main(step=RuntimeError('unexpected'))
        self.assertEqual(code, 2)
        m['step'].assert_called_once()

    def test_catch_up_sends_once_through_the_real_step(self):
        code, m = self.run_main(step=False)
        self.assertEqual(code, 2)
        m['send'].assert_called_once_with(m['message'], m['zero_ledger'].return_value, '2026-10-19')
        self.assertEqual(self.saved()['status'], 'sent')
        m['final_send'].assert_not_called()


def template(company, rows):
    lines = ['!Do not change controls',
             '!CO CODE,BATCH ID,FILE #,BATCH DESCRIPTION,Name,Rate,Reg Hours,O/T Hours,Other Earnings - TIP EARNS',
             '!,,,,,,,,']
    lines += [f'{company},Batch1,{number},,{name},H 15.0000,999,999,999' for number, name in rows]
    lines += ['!Column7,Reg Hours,,Reg Hours,,,,80', '!Column8,O/T Hours,,O/T Hours,,,,80',
              '!Column9,Other Earnings - TIP EARNS,,Earnings 3 Code,Earnings 3 Amount,T,,0']
    return ('\r\n'.join(lines) + '\r\n').encode()


TEMPLATES = {'AZ': template('FNC', [('099101', 'Quill Ambersong')]),
             'CO': template('FND', [('099201', 'Odessa Brightwater')]),
             'TX': template('ZKT', [('099301', 'Rowan Featherby')])}


def final_payroll():
    roster = [('9001', 'Quill', 'Ambersong', '3759-01', ['crew'], '10.00', '0.00'),
              ('9002', 'Odessa', 'Brightwater', '3356-01', ['crew'], '8.00', '0.00'),
              ('9003', 'Rowan', 'Featherby', '3910-01', ['manager'], '40.00', '2.00')]
    return {'period': DATES, 'verified': True, 'issues': [], 'warnings': [],
            'roster': [{'geid': g, 'first_name': f, 'last_name': l, 'roles': r} for g, f, l, _, r, _, _ in roster],
            'employees': [{'store': s, 'employee_id': g, 'name': f + ' ' + l, 'roles': r, 'regular_hours': reg,
                           'overtime_hours': ot, 'total_hours': f'{float(reg) + float(ot):.2f}'}
                          for g, f, l, s, r, reg, ot in roster]}


class FinalPayrollNotesTests(unittest.TestCase):
    """prepare() records live-roster and identity notes as warnings; readiness never changes."""
    def prepare(self, *, adp=False, identities='', rows=None, problem=None):
        env = {'GITHUB_ACTIONS': 'true', 'PAYROLL_ADP_IDENTITIES': identities,
               'ADP_ROSTER_SETTINGS': json.dumps({'period': DATES})}
        env.update({'ADP_' + code + '_TEMPLATE': base64.b64encode(raw).decode() for code, raw in TEMPLATES.items()})
        if adp:
            env.update(ADP_ENV)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        output = Path(folder.name)
        with patch.dict(os.environ, env, clear=True), \
                patch('adp_api.workers_once', return_value=(None, problem) if problem else (rows or [], None)), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            payroll_run.prepare(tips(), final_payroll(), output)
        audit = json.loads((output / 'adp-readiness.json').read_text(encoding='utf-8'))
        checks = (output / 'ADP import checks.txt').read_text(encoding='utf-8')
        return audit, checks, printed.getvalue(), output

    def core(self, audit):
        return (audit['ready'], audit['reports_deliverable'], audit['issues'], audit['missing'],
                {code: (d['ready'], d['issues'], d['missing'], d['output_sha256']) for code, d in audit['regions'].items()})

    def test_unavailable_roster_and_invalid_identity_list_are_visible_without_changing_readiness(self):
        baseline, baseline_checks, _, _ = self.prepare()
        self.assertTrue(baseline['ready'])
        self.assertEqual(baseline['regions']['AZ']['roster_notes'], [])
        self.assertEqual(baseline['regions']['AZ']['identity_notes'], [])
        self.assertNotIn('note:', baseline_checks)
        audit, checks, printed, _ = self.prepare(adp=True, identities='{"version": 2, "identities": []}',
                                                 problem='ADP token request returned HTTP 503')
        self.assertEqual(self.core(audit), self.core(baseline))
        for code in ('AZ', 'CO', 'TX'):
            detail = audit['regions'][code]
            unavailable = (f'{code}: live ADP roster unavailable (ADP token request returned HTTP 503); '
                           'CSV uses the last supplied export only.')
            self.assertEqual(detail['roster_notes'], [unavailable])
            self.assertEqual(detail['identity_notes'], [adp_identities.INVALID])
            self.assertEqual(detail['warnings'][:2], [unavailable, adp_identities.INVALID])
            self.assertIn('Live ADP roster note: ' + unavailable, checks)
            self.assertIn('Identity note: ' + adp_identities.INVALID, checks)
        self.assertIn(adp_identities.INVALID, printed)
        self.assertIn('AZ: 1 live ADP roster note(s) and 1 confirmed-identity note(s)', printed)

    def test_stale_confirmed_identity_is_a_note_not_an_issue(self):
        stale = dict(SECRET_ENTRY, geid='9099', company='FNC', file_number='099999', tcp_name='Nobody Here',
                     adp_name='Nobody There')
        audit, checks, printed, _ = self.prepare(identities=json.dumps({'version': 1, 'identities': [stale]}))
        note = audit['regions']['AZ']['identity_notes']
        self.assertEqual(len(note), 1)
        self.assertIn('AZ GEID 9099: confirmed ADP file 099999 is not on the ADP roster', note[0])
        self.assertTrue(audit['ready'])
        self.assertEqual(audit['issues'], [])
        self.assertIn('Identity note: ' + note[0], checks)
        self.assertNotIn('9099', printed)
        self.assertNotIn('099999', printed)

    def test_rows_added_from_the_live_roster_are_listed(self):
        rows = workers() + [worker('Juniper', 'Quayle', 'FNC', '099105', hired='2026-10-01', rate='16.50')]
        audit, checks, printed, output = self.prepare(adp=True, rows=rows)
        az = audit['regions']['AZ']
        self.assertTrue(audit['ready'])
        self.assertEqual(az['adp_added'], [{'file_number': '99105', 'name': 'Juniper Quayle', 'rate': 'H 16.5000',
                                            'hire_date': '2026-10-01'}])
        self.assertEqual(len(az['roster_notes']), 1)
        self.assertIn('AZ: live ADP roster added 1 active worker(s)', az['roster_notes'][0])
        self.assertIn('Rows added from the live ADP roster (not on the last export): ADP file 99105', checks)
        self.assertIn(b'FNC,Batch1,99105,,Juniper Quayle,H 16.5000,0.00,0.00,0.00', (output / 'EPIFNC00.CSV').read_bytes())
        self.assertNotIn('Juniper', printed)


class BuildImportsNotesTests(unittest.TestCase):
    def build(self, settings):
        summary = {'period': DATES, 'employees': [
            {'geid': '9003', 'first_name': 'Rowan', 'last_name': 'Featherby', 'stores': ['3910-01']}],
            'source_rows': [{'store': '3910-01', 'geid': '9003', 'regular_hours': '40.00',
                             'overtime_hours': '2.00', 'tips': '0.00'}]}
        with tempfile.TemporaryDirectory() as folder:
            templates = Path(folder) / 'templates'
            templates.mkdir()
            (templates / 'EPIZKT00.CSV').write_bytes(TEMPLATES['TX'])
            return build_imports(summary, templates, Path(folder) / 'out', settings)

    def test_missing_or_malformed_notes_are_ignored(self):
        plain = self.build({})['regions']['TX']
        self.assertEqual((plain['roster_notes'], plain['identity_notes'], plain['adp_added']), ([], [], []))
        self.assertTrue(plain['ready'])
        for bad in ([], 'text', {'TX': 'text'}, {'TX': {'roster_notes': 'text', 'identity_notes': [1, None],
                                                        'adp_added': ['x']}}):
            with self.subTest(bad=bad):
                detail = self.build({'roster_notes': bad})['regions']['TX']
                self.assertTrue(detail['ready'])
                self.assertEqual(detail['warnings'], plain['warnings'])
                self.assertEqual(detail['identity_notes'], [])


if __name__ == '__main__':
    unittest.main()
