import copy
import io
import json
from contextlib import redirect_stdout
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

import altametrics
from altametrics_session import make_session, validate_session, secret_name


def session():
    return {'version': 1, 'region': 'CO',
            'entry_url': 'https://littlecaesars.altametrics.com/ers/ers_webApp',
            'state': {'cookies': [{'name': 'session', 'value': 'sensitive-test-value',
                                   'domain': '.altametrics.com', 'path': '/'}], 'origins': []}}


class SessionTests(unittest.TestCase):
    def test_roundtrip_and_region_binding(self):
        value = session()
        self.assertEqual(validate_session(json.dumps(value), 'CO'), value)
        with self.assertRaises(ValueError):
            validate_session(value, 'AZ')
        with self.assertRaises(ValueError):
            secret_name('OTHER')

    def test_external_state_is_filtered(self):
        value = session()
        state = copy.deepcopy(value['state'])
        state['cookies'].append({'name': 'unrelated', 'value': 'never-save', 'domain': '.google.com'})
        state['origins'] = [{'origin': 'https://google.com', 'localStorage': []}]
        result = make_session('CO', value['entry_url'], state)
        self.assertEqual(result, value)

    def test_invalid_state_errors_do_not_include_secrets(self):
        cases = []
        for url in ('https://altametrics.com.evil.test/ers_webApp',
                    'http://altametrics.com/ers_webApp',
                    'https://user:password@altametrics.com/ers_webApp',
                    'https://altametrics.com/login'):
            value = session()
            value['entry_url'] = url
            cases.append(value)
        value = session()
        value['state']['cookies'][0]['domain'] = 'evil.test'
        cases.append(value)
        value = session()
        value['state']['cookies'][0]['value'] = 'x' * 40001
        cases.extend([value, {}, 'invalid-sensitive-test-value'])
        for value in cases:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(ValueError, '^Invalid Altametrics session; reconnect the selected region$'):
                    validate_session(value, 'CO')

    def test_daily_fresh_login_requires_credentials_even_with_saved_session(self):
        with patch.dict(os.environ, {'CO_ALTAMETRICS_SESSION': json.dumps(session())}, clear=True),                 patch.object(altametrics, 'gateway_reader', return_value=False):
            self.assertEqual(altametrics.missing_secrets(), ['COLORADO_USERNAME', 'COLORADO_PASSWORD', 'AZ_USERNAME', 'AZ_PASSWORD', 'TX_USERNAME', 'TX_PASSWORD'])


class LoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_supplier_notice_and_redirect_finish_before_credentials(self):
        page = Mock(url='https://apssopgs.altametrics.com/notice', wait_for_timeout=AsyncMock())
        body = Mock(inner_text=AsyncMock(return_value='Important Message Regarding: Crazy Sauce Cups/Lids'))
        notice = Mock(count=AsyncMock(return_value=1))
        password = Mock(count=AsyncMock(return_value=0))
        dashboard = Mock(count=AsyncMock(return_value=1))

        async def click():
            body.inner_text.return_value = 'This page will refresh on its own'

        async def wait(ms):
            if ms == 1000:
                page.url = session()['entry_url']

        notice.click = AsyncMock(side_effect=click)
        page.wait_for_timeout.side_effect = wait
        page.locator.side_effect = lambda selector: {'body': body,
            'input#Submitbutton[type="submit"]:visible': notice,
            'input[type="password"]:visible': password,
            '#FILTER_STORE_GO:visible': dashboard,
            altametrics.ENTRY_FIELDS: Mock(count=AsyncMock(return_value=0))}[selector]
        with patch.object(altametrics, 'guard', AsyncMock()), \
             patch.object(altametrics, 'dismiss_popups', AsyncMock()):
            self.assertEqual(await altametrics.wait_login_state(page), 'dashboard')
        notice.click.assert_awaited_once()

    async def test_scheduled_maintenance_announcement_is_acknowledged_once_per_page(self):
        # October 2026 SSO announcement: full-page notice with one submit control.
        page = Mock(url='https://apssopgs.altametrics.com/notice', wait_for_timeout=AsyncMock())
        body = Mock(inner_text=AsyncMock(return_value='Scheduled Maintenance 10/14/2026 - 10/15/2026 '
            'Overview Altametrics has planned system maintenance. Users may log into the application once complete.'))
        notice = Mock(count=AsyncMock(return_value=1))
        password = Mock(count=AsyncMock(return_value=0))
        dashboard = Mock(count=AsyncMock(return_value=0))

        async def click():
            body.inner_text.return_value = 'Sign in'
            password.count.return_value = 1

        notice.click = AsyncMock(side_effect=click)
        page.locator.side_effect = lambda selector: {'body': body,
            'input#Submitbutton[type="submit"]:visible': notice,
            'input[type="password"]:visible': password,
            '#FILTER_STORE_GO:visible': dashboard,
            altametrics.ENTRY_FIELDS: Mock(count=AsyncMock(return_value=0))}[selector]
        with patch.object(altametrics, 'guard', AsyncMock()):
            self.assertEqual(await altametrics.wait_login_state(page), 'credentials')
        notice.click.assert_awaited_once()

    async def test_agreement_or_credential_pages_with_submit_control_are_not_acknowledged(self):
        for text, passwords in (('Important notice: please accept the updated terms', 0),
                                ('Privacy Notice. By clicking Submit you acknowledge the updated privacy notice.', 0),
                                ('License Notice. Please review the updated software license before continuing.', 0),
                                ('Notice: updated Conditions of Use apply. Click Submit to continue.', 0),
                                ('Important message: enter the one-time passcode we sent you', 0),
                                ('Important message regarding your password', 1),
                                ('Reminder: verify you are human', 0)):
            with self.subTest(text=text):
                page = Mock(url='https://apssopgs.altametrics.com/notice', wait_for_timeout=AsyncMock())
                body = Mock(inner_text=AsyncMock(return_value=text))
                notice = Mock(count=AsyncMock(return_value=1), click=AsyncMock())
                password = Mock(count=AsyncMock(return_value=passwords))
                dashboard = Mock(count=AsyncMock(return_value=0))
                page.locator.side_effect = lambda selector: {'body': body,
                    'input#Submitbutton[type="submit"]:visible': notice,
                    'input[type="password"]:visible': password,
                    '#FILTER_STORE_GO:visible': dashboard,
                    altametrics.ENTRY_FIELDS: Mock(count=AsyncMock(return_value=0))}[selector]
                with patch.object(altametrics, 'guard', AsyncMock()):
                    if passwords:
                        self.assertEqual(await altametrics.wait_login_state(page), 'credentials')
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'redirect did not finish'):
                            await altametrics.wait_login_state(page)
                notice.click.assert_not_awaited()
        self.assertIsNone(altametrics.announcement_kind('Security Check: please verify you are not a robot'))
        self.assertEqual(altametrics.announcement_kind('IMPORTANT MESSAGE REGARDING: Crazy Sauce Cups/Lids'), 'important message')
        self.assertEqual(altametrics.classify_announcement('Account security notice'), (None, 'page mentions "security"'))
        self.assertEqual(altametrics.classify_announcement('System Upgrade tonight'), (None, 'no announcement keyword'))

    async def test_announcement_with_entry_fields_is_a_form_and_not_acknowledged(self):
        page = Mock(url='https://apssopgs.altametrics.com/notice', wait_for_timeout=AsyncMock())
        body = Mock(inner_text=AsyncMock(return_value='Important notice: enter the code from your authenticator'))
        notice = Mock(count=AsyncMock(return_value=1), click=AsyncMock())
        page.locator.side_effect = lambda selector: {'body': body,
            'input#Submitbutton[type="submit"]:visible': notice,
            'input[type="password"]:visible': Mock(count=AsyncMock(return_value=0)),
            '#FILTER_STORE_GO:visible': Mock(count=AsyncMock(return_value=0)),
            altametrics.ENTRY_FIELDS: Mock(count=AsyncMock(return_value=1))}[selector]
        with patch.object(altametrics, 'guard', AsyncMock()), redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(RuntimeError, 'redirect did not finish'):
                await altametrics.wait_login_state(page)
        notice.click.assert_not_awaited()
        self.assertEqual(output.getvalue().count('not acknowledged: page has entry fields'), 1)

    def expiry_page(self, controls):
        page = Mock(url='https://app108b.altametrics.com/ers_webApp/change', wait_for_timeout=AsyncMock())
        body = Mock(inner_text=AsyncMock(return_value='Create New Password\nYour password will expire in 5 days. Please change your password'))
        dashboard = Mock(count=AsyncMock(return_value=0))
        passwords = Mock(count=AsyncMock(return_value=3))
        located = Mock(count=AsyncMock(return_value=len(controls)))
        located.nth.side_effect = lambda i: controls[i]
        page.locator.side_effect = lambda selector: {'body': body,
            'input[type="password"]:visible': passwords,
            '#FILTER_STORE_GO:visible': dashboard,
            altametrics.POSTPONE_CONTROLS: located}[selector]
        return page, dashboard, passwords

    def control(self, label, kind='value'):
        control = Mock(click=AsyncMock())
        control.get_attribute = AsyncMock(return_value=label if kind == 'value' else None)
        control.inner_text = AsyncMock(return_value=label if kind == 'text' else '')
        return control

    async def test_password_expiry_warning_is_postponed_through_its_own_control_only(self):
        self.assertIn('input[type="submit"]:visible', altametrics.POSTPONE_CONTROLS)  # 'Cancel' is a submit input.
        self.assertTrue(altametrics.POSTPONE_LABELS.fullmatch('Cancel'))
        self.assertIsNone(altametrics.POSTPONE_LABELS.fullmatch('Change Password'))
        submit, later = self.control('Change Password'), self.control('Remind me later', kind='text')
        page, dashboard, passwords = self.expiry_page([submit, later])

        async def click():
            dashboard.count.return_value = 1
            passwords.count.return_value = 0

        later.click.side_effect = click
        with patch.object(altametrics, 'guard', AsyncMock()), patch.object(altametrics, 'dismiss_popups', AsyncMock()), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(await altametrics.wait_login_state(page), 'dashboard')
        submit.click.assert_not_awaited()
        later.click.assert_awaited_once()
        self.assertIn('postponed through its "Remind me later" control', output.getvalue())

    async def test_password_change_without_postpone_control_stops_with_clear_reason(self):
        page, _, _ = self.expiry_page([self.control('Submit')])
        with patch.object(altametrics, 'guard', AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, 'requires a password change'):
                await altametrics.wait_login_state(page)
        # A page that persists after one postponement is also a required change, not a loop.
        later = self.control('Later')
        page, _, _ = self.expiry_page([later])
        with patch.object(altametrics, 'guard', AsyncMock()), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'requires a password change'):
                await altametrics.wait_login_state(page)
        later.click.assert_awaited_once()

    async def test_security_prompt_stops_sso_wait(self):
        page = Mock()
        with patch.object(altametrics, 'guard', AsyncMock(side_effect=RuntimeError('interactive verification'))):
            with self.assertRaisesRegex(RuntimeError, 'interactive'):
                await altametrics.wait_login_state(page)
        page.locator.assert_not_called()

    async def test_security_prompt_is_not_dismissed_as_a_popup(self):
        page = Mock()
        with patch.object(altametrics, 'guard', AsyncMock(side_effect=RuntimeError('interactive verification'))):
            with self.assertRaisesRegex(RuntimeError, 'interactive'):
                await altametrics.dismiss_popups(page)
        page.locator.assert_not_called()

    async def test_ordinary_dialog_closed_by_its_button(self):
        button = Mock(is_visible=AsyncMock(return_value=True), click=AsyncMock())
        buttons = Mock(count=AsyncMock(return_value=1), first=button)
        dialog = Mock()
        dialog.get_by_role.return_value = buttons
        dialogs = Mock(count=AsyncMock(return_value=1))
        dialogs.nth.return_value = dialog
        page = Mock()
        page.locator.return_value = dialogs
        with patch.object(altametrics, 'guard', AsyncMock()) as guard:
            await altametrics.dismiss_popups(page)
        button.click.assert_awaited_once()
        self.assertEqual(guard.await_count, 2)

    async def test_expired_session_stops_without_password_fallback(self):
        page = Mock(goto=AsyncMock())
        with patch.object(altametrics, 'settle', AsyncMock(side_effect=RuntimeError('Security Check'))), \
             patch.object(altametrics, 'fill_first', AsyncMock()) as fill:
            with self.assertRaisesRegex(RuntimeError, 'CO: saved session needs interactive reconnection'):
                await altametrics.login(page, 'CO', session())
            fill.assert_not_called()

    async def test_wrong_regional_account_is_rejected(self):
        field = Mock(count=AsyncMock(return_value=1), input_value=AsyncMock(return_value='03759-00001'))
        page = Mock(url=session()['entry_url'])
        page.locator.return_value.locator.return_value.locator.return_value = field
        with patch.object(altametrics, 'guard', AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                await altametrics.verify_account_region(page, 'CO')
            await altametrics.verify_account_region(page, 'AZ')

    async def test_visible_security_check_is_never_ignored(self):
        page = Mock(frames=[])
        page.locator.return_value.inner_text = AsyncMock(return_value='Security Check')
        with self.assertRaisesRegex(RuntimeError, 'interactive'):
            await altametrics.guard(page)

    async def test_badge_hidden_and_completed_frames_vs_unresolved_challenge(self):
        for visible, badge, checked, blocked in ((False, False, 'false', False),
                                                (True, True, 'false', False),
                                                (True, False, 'true', False),
                                                (True, False, 'false', True)):
            element = Mock(is_visible=AsyncMock(return_value=visible), evaluate=AsyncMock(return_value=badge))
            frame = Mock(url='https://www.google.com/recaptcha/api2/anchor', frame_element=AsyncMock(return_value=element))
            frame.locator.return_value.count = AsyncMock(return_value=1)
            frame.locator.return_value.get_attribute = AsyncMock(return_value=checked)
            page = Mock(frames=[frame])
            page.locator.return_value.inner_text = AsyncMock(return_value='Dashboard')
            if blocked:
                with self.assertRaisesRegex(RuntimeError, 'interactive verification'):
                    await altametrics.guard(page)
            else:
                await altametrics.guard(page)
