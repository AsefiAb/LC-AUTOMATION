"""Read-only: list Gateway's Reporting menu and optionally open one report for one store/day.

Used to find where daily sales and transaction counts live. No emails or updates.
"""
import argparse
import asyncio
import json
import os
import re
from pathlib import Path


async def main(report, store, day):
    from playwright.async_api import async_playwright
    out = Path(__file__).parent / 'reports' / 'gateway-menu'
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context(viewport={'width': 1600, 'height': 1000})).new_page()
        page.set_default_timeout(30000)
        await page.goto('https://www.lcegateway.com/login', wait_until='domcontentloaded')
        await page.locator('input[name="username"], input[type="text"]').first.fill(os.environ['CO_GATEWAY_USER'])
        await page.locator('input[type="password"]').first.fill(os.environ['CO_GATEWAY_PASS'])
        await page.locator('button[type="submit"], input[type="submit"], button:has-text("Login")').first.click()
        await page.wait_for_timeout(4000)
        await page.screenshot(path=str(out / 'home.png'), full_page=True)
        await page.locator('a[href*="Reporting"], a[href*="reporting"], [title*="Reporting"], [aria-label*="Reporting"]').first.click()
        await page.wait_for_timeout(5000)
        await page.screenshot(path=str(out / 'reporting.png'), full_page=True)
        links = await page.locator('a, button, [role="menuitem"], [role="treeitem"], li').evaluate_all(
            'els => els.map(e => (e.innerText || "").trim()).filter(t => t && t.length < 80)')
        menu = sorted(set(links))
        (out / 'menu.json').write_text(json.dumps({'url': page.url, 'items': menu}, indent=2), encoding='utf-8')
        print(json.dumps(menu, indent=1))
        if report:
            await page.get_by_text(re.compile(r'^\s*' + re.escape(report) + r'\s*$', re.I)).first.click()
            await page.wait_for_timeout(15000)
            await page.screenshot(path=str(out / 'report.png'), full_page=True)
            for i, frame in enumerate(page.frames):
                try:
                    text = await frame.locator('body').inner_text()
                except Exception:
                    continue
                (out / f'report-frame-{i}.txt').write_text(frame.url + '\n\n' + text, encoding='utf-8')
        await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', default='')
    parser.add_argument('--store', default='')
    parser.add_argument('--day', default='')
    args = parser.parse_args()
    asyncio.run(main(args.report, args.store, args.day))
