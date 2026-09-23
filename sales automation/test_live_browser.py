import unittest
from unittest.mock import AsyncMock, Mock, patch

import live_browser


class LiveBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_connects_only_to_loopback_and_keeps_the_existing_page(self):
        page = Mock(url='https://app108b.altametrics.com/ers/ers_webApp')
        browser = Mock(contexts=[Mock(pages=[page])])
        playwright = Mock()
        playwright.chromium.connect_over_cdp = AsyncMock(return_value=browser)
        with patch.object(live_browser, 'require_local_windows'):
            self.assertIs(await live_browser.page_for(playwright, 'CO'), page)
        playwright.chromium.connect_over_cdp.assert_awaited_once_with('http://127.0.0.1:9331', timeout=10000)
        page.goto.assert_not_called()
        browser.close.assert_not_called()

    async def test_ambiguous_tabs_are_not_selected_arbitrarily(self):
        page = Mock(url='https://app108b.altametrics.com/ers/ers_webApp')
        playwright = Mock()
        playwright.chromium.connect_over_cdp = AsyncMock(return_value=Mock(contexts=[Mock(pages=[page, page])]))
        with patch.object(live_browser, 'require_local_windows'):
            with self.assertRaisesRegex(RuntimeError, 'exactly one'):
                await live_browser.page_for(playwright, 'TX')

    async def test_closed_browser_gives_reconnection_instruction_without_transport_details(self):
        playwright = Mock()
        playwright.chromium.connect_over_cdp = AsyncMock(side_effect=Exception('private-transport-detail'))
        with patch.object(live_browser, 'require_local_windows'):
            with self.assertRaisesRegex(RuntimeError, '^AZ: open its dedicated sales Chrome window and sign in$'):
                await live_browser.page_for(playwright, 'AZ')
