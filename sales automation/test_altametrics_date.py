import unittest
from unittest.mock import AsyncMock, patch
from playwright.async_api import async_playwright

from altametrics import select_date, report_tables, parse_tables


class DateWidgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_split_header_footer_stay_in_their_own_report(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                header = '<table><thead><tr><th>Time</th><th>Product Net</th><th>Actual Total Trans</th><th></th><th></th><th></th><th></th><th>Punched Labor %</th></tr></thead></table>'
                total = '<table><tfoot><tr><td>Actual Total</td><td>$8,185.65</td><td>359</td><td></td><td>83:35</td><td></td><td></td><td>17.12</td></tr></tfoot></table>'
                await page.set_content('<div class="dataTables_wrapper">' + header + total + '</div>')
                self.assertEqual(parse_tables(await report_tables(page)), {'sales': '8185.65', 'transactions': 359, 'labor_pct': '17.12'})
                await page.set_content('<div class="dataTables_wrapper">' + header + '</div><div class="dataTables_wrapper">' + total + '</div>')
                with self.assertRaises(ValueError):
                    parse_tables(await report_tables(page))
            finally:
                await browser.close()

    async def test_keyboard_backed_date_value_and_server_date_check(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content('''<form onsubmit="event.preventDefault(); document.querySelector('#day').value = window.savedDay">
                  <input id="day" value="09/20/2026" onkeyup="window.savedDay=this.value">
                  <input type="submit" value="GO">
                </form><script>window.savedDay='09/20/2026'</script>''')
                # Reproduce a widget where merely filling text leaves the
                # submitted date unchanged.
                await page.locator('#day').fill('09/19/2026')
                await page.locator('input[type=submit]').click()
                self.assertEqual(await page.locator('#day').input_value(), '09/20/2026')
                with patch('altametrics.settle', AsyncMock()):
                    await select_date(page, '2026-09-19')
                    self.assertEqual(await page.locator('#day').input_value(), '09/19/2026')
                    # A server ignoring the requested date must still stop.
                    await page.locator('form').evaluate("el => el.onsubmit = e => {e.preventDefault(); document.querySelector('#day').value='09/20/2026'}")
                    with self.assertRaisesRegex(RuntimeError, 'did not retain the requested date'):
                        await select_date(page, '2026-09-19')
            finally:
                await browser.close()
