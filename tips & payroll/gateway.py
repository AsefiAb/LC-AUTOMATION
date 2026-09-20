"""Gateway financial report tips; regional credentials never overlap Altametrics."""
import asyncio
import os
import re
import json
from pathlib import Path
from urllib.parse import urlsplit
from datetime import date
from decimal import Decimal

REGIONS = {'CO': ('3356', 7), 'AZ': ('3759', 7), 'TX': ('3910', 4)}
LABELS = ('InStore Tips (+)', 'Prepaid InStore Tips (+)')


class GatewayError(RuntimeError):
    pass


def missing_secrets():
    return [f'{region}_GATEWAY_{suffix}' for region in REGIONS for suffix in ('USER', 'PASS')
            if not os.environ.get(f'{region}_GATEWAY_{suffix}')]


def parse_tip_rows(tables):
    found = {}
    for table in tables:
        for cells in table:
            if len(cells) < 3 or cells[0].strip() not in LABELS:
                continue
            label = cells[0].strip()
            if label in found:
                raise ValueError('Gateway returned duplicate tip rows')
            # Original Financial View export: total is penultimate cell, last blank.
            if cells[-1].strip():
                raise ValueError('Gateway Financial View column layout changed')
            value = cells[-2].strip().replace('$', '').replace(',', '')
            if not re.fullmatch(r'\d+(?:\.\d{1,2})?', value):
                raise ValueError('Gateway tip amount is missing or invalid')
            found[label] = Decimal(value)
    if set(found) != set(LABELS):
        raise ValueError('Both Gateway tip lines must be present; blanks are not zero')
    return {'instore_tips': f'{found[LABELS[0]]:.2f}', 'prepaid_instore_tips': f'{found[LABELS[1]]:.2f}',
            'total_tips': f'{sum(found.values()):.2f}'}


async def collect(dates):
    if missing_secrets():
        raise RuntimeError('Missing Gateway secrets: ' + ', '.join(missing_secrets()))
    from playwright.async_api import async_playwright
    start = date.fromisoformat(dates['start_date']).strftime('%m/%d/%Y')
    stop = date.fromisoformat(dates['end_date']).strftime('%m/%d/%Y')
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            async def region_report(region, franchise, count):
                context = await browser.new_context()
                stage = 'login'
                try:
                    page = await context.new_page()
                    page.set_default_timeout(30000)
                    await page.goto('https://www.lcegateway.com/login', wait_until='domcontentloaded')
                    await page.locator('input[name="username"], input[type="text"]').first.fill(os.environ[f'{region}_GATEWAY_USER'])
                    await page.locator('input[type="password"]').first.fill(os.environ[f'{region}_GATEWAY_PASS'])
                    await page.locator('button[type="submit"], input[type="submit"], button:has-text("Login")').first.click()
                    await page.wait_for_timeout(2000)
                    stage = 'report navigation'
                    # Follow the authenticated site's actual reporting route;
                    # the old fallback /Reporting URL now returns page-not-found.
                    await page.locator('a[href*="Reporting"], a[href*="reporting"], [title*="Reporting"], [aria-label*="Reporting"]').first.click()
                    await page.get_by_text('FINANCIAL VIEW', exact=True).click()
                    await page.locator('iframe[src*="powerbi.com"]').first.wait_for()
                    frame = None
                    for _ in range(40):
                        frame = next((f for f in page.frames if 'paginated-reports.powerbi.com' in f.url), None)
                        if frame:
                            break
                        await page.wait_for_timeout(500)
                    if not frame:
                        raise RuntimeError('Gateway report frame unavailable; check login/2FA')
                    print(f'{region}: Gateway Financial View opened', flush=True)
                    result = []
                    for index in range(1, count + 1):
                        stage = 'date parameters'
                        if not await frame.locator('#datePicker-input4').is_visible():
                            await frame.locator('[title*="Parameters"], button:has-text("Parameters")').first.click()
                        for selector, value in (('#datePicker-input4', start), ('#datePicker-input9', stop)):
                            await frame.locator(selector).fill(value)
                            await frame.locator(selector).press('Tab')
                            if await frame.locator(selector).input_value() != value:
                                raise RuntimeError('Gateway report date did not match')
                        dropdown = frame.locator('#LocationNumber')
                        stage = 'store selection'
                        await dropdown.click()
                        # Read the checkbox labels/state, not fixed screen coordinates.
                        options = frame.get_by_role('checkbox')
                        target = f'{index:05d}'
                        matched = False
                        for i in range(await options.count()):
                            option = options.nth(i)
                            text = (await option.get_attribute('aria-label') or await option.inner_text()).strip()
                            if re.search(r'select all', text, re.I):
                                if await option.is_checked():
                                    await option.uncheck()
                        for i in range(await options.count()):
                            option = options.nth(i)
                            text = (await option.get_attribute('aria-label') or await option.inner_text()).strip()
                            if re.fullmatch(r'\d{5}', text):
                                await option.set_checked(text == target)
                                matched = matched or text == target
                        if not matched:
                            raise RuntimeError('Gateway store options could not be identified safely')
                        await dropdown.press('Escape')
                        selected = set(re.findall(r'\b\d{5}\b', await dropdown.input_value()))
                        if selected != {target}:
                            raise RuntimeError('Gateway selection is not exactly one requested store')
                        stage = 'report refresh'
                        old_report = await frame.locator('table').evaluate_all('tables => tables.filter(t => t.innerText.includes("InStore Tips (+)")).map(t => t.innerText).join("\\n")')
                        await frame.locator('button:has-text("View Report"), button:has-text("View report"), input[value="View Report"]').first.click()
                        try:
                            await frame.get_by_text('Loading report', exact=False).wait_for(state='visible', timeout=5000)
                        except Exception:
                            pass
                        await frame.get_by_text('Loading report', exact=False).wait_for(state='hidden', timeout=60000)
                        await frame.get_by_text(LABELS[0], exact=True).first.wait_for()
                        await page.wait_for_timeout(1000)
                        if old_report:
                            await frame.wait_for_function('old => {const text=[...document.querySelectorAll("table")].filter(t=>t.innerText.includes("InStore Tips (+)")).map(t=>t.innerText).join("\\n"); return text && text!==old}', arg=old_report, timeout=30000)
                        if await frame.locator('#datePicker-input4').input_value() != start or await frame.locator('#datePicker-input9').input_value() != stop:
                            raise RuntimeError('Gateway report dates changed during refresh')
                        if set(re.findall(r'\b\d{5}\b', await dropdown.input_value())) != {target}:
                            raise RuntimeError('Gateway store changed during refresh')
                        tables = await frame.locator('table').evaluate_all('tables => tables.map(t => [...t.rows].map(r => [...r.cells].map(c => c.innerText.trim())))')
                        stage = 'tip parsing'
                        result.append({'store': f'{franchise}-{index:02d}', **parse_tip_rows(tables)})
                    return result
                except Exception as exc:
                    try:
                        folder = Path(__file__).parent / 'reports' / 'gateway-diagnostics'
                        folder.mkdir(parents=True, exist_ok=True)
                        await page.screenshot(path=str(folder / (region + '.png')), mask=[page.locator('input')])
                        metadata = {'stage': stage, 'host': urlsplit(page.url).hostname,
                                    'frame_hosts': [urlsplit(f.url).hostname for f in page.frames],
                                    'inputs': await page.locator('input:visible').evaluate_all('els=>els.map(e=>({id:e.id,type:e.type}))')}
                        (folder / (region + '.json')).write_text(json.dumps(metadata, indent=2), encoding='utf-8')
                    except Exception:
                        pass
                    detail = str(exc) if type(exc) in (RuntimeError, ValueError) else type(exc).__name__
                    raise GatewayError(f'{region}: Gateway {stage} failed: {detail}') from None
                finally:
                    await context.close()
            groups = await asyncio.gather(*(region_report(region, *definition) for region, definition in REGIONS.items()), return_exceptions=True)
            failures = [str(g) for g in groups if isinstance(g, GatewayError)]
            if failures:
                raise GatewayError('; '.join(failures))
            if any(isinstance(g, BaseException) for g in groups):
                raise GatewayError('Gateway browser session failed')
            return {'verified': True, 'issues': [], 'period': dates, 'stores': [r for group in groups for r in group]}
        finally:
            await browser.close()


def fetch(dates):
    return asyncio.run(collect(dates))
