import copy
import json
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

    def test_session_replaces_credentials_for_only_its_region(self):
        with patch.dict(os.environ, {'CO_ALTAMETRICS_SESSION': json.dumps(session())}, clear=True):
            self.assertEqual(altametrics.missing_secrets(), ['AZ_USERNAME', 'AZ_PASSWORD', 'TX_USERNAME', 'TX_PASSWORD'])


class LoginTests(unittest.IsolatedAsyncioTestCase):
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
