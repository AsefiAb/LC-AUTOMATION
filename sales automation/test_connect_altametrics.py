import unittest
from unittest.mock import AsyncMock, Mock, patch

from connect_altametrics import verify_reusable_session


class ReusableSessionTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        url = 'https://app108b.altametrics.com/ers/ers_webApp'
        original = {'cookies': [{'domain': '.altametrics.com', 'name': 'session', 'value': 'old'}], 'origins': []}
        refreshed = {'cookies': [{'domain': '.altametrics.com', 'name': 'session', 'value': 'rotated'}], 'origins': []}
        page = Mock(url=url, goto=AsyncMock())
        page.locator.return_value.wait_for = AsyncMock()
        context = Mock(new_page=AsyncMock(return_value=page), storage_state=AsyncMock(return_value=refreshed))
        browser = Mock(new_context=AsyncMock(return_value=context), close=AsyncMock())
        runtime = Mock()
        runtime.chromium.launch = AsyncMock(return_value=browser)
        return runtime, browser, page, {'state': original, 'entry_url': url}, refreshed

    async def test_returns_refreshed_state_after_successful_headless_check(self):
        runtime, browser, page, state, refreshed = self.fixture()
        with patch('connect_altametrics.verify_account_region', AsyncMock()) as verify:
            saved = await verify_reusable_session(runtime, 'CO', state)
        runtime.chromium.launch.assert_awaited_once_with(headless=True)
        verify.assert_awaited_once_with(page, 'CO')
        self.assertEqual(saved['state'], refreshed)
        browser.close.assert_awaited_once()

    async def test_failed_verification_does_not_return_a_saveable_session(self):
        runtime, browser, _, state, _ = self.fixture()
        with patch('connect_altametrics.verify_account_region', AsyncMock(side_effect=RuntimeError('Session rejected'))):
            with self.assertRaisesRegex(RuntimeError, 'Session rejected'):
                await verify_reusable_session(runtime, 'CO', state)
        browser.close.assert_awaited_once()
