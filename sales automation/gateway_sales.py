"""Daily store sales and customer counts from Gateway's Franchise Sales Roll-up.

Gateway (lcegateway.com) has no CAPTCHA, so this runs on GitHub-hosted runners
with the same per-region Gateway logins payroll uses. Reporting > CV Sales >
Franchise Sales Roll-up lists every store in the franchise for one date:
Royalty-Obligation is the workbook's sales (Altametrics Product Net) and
Customer-Count its transactions; on 2026-10-26 they matched the Altametrics
Daily Activity Report to the cent.

Records keep the Altametrics shape so validation, workbooks and email are
unchanged. Gateway has no labor; labor stays the TCP wage estimate.
"""
import asyncio
import json
import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

LOGIN_URL = 'https://www.lcegateway.com/login'
REPORT = 'FRANCHISE SALES ROLL-UP'
FRANCHISES = {'CO': '3356', 'AZ': '3759', 'TX': '3910'}
COLUMNS = ['Franchise-Number', 'Store-Number', 'Royalty-Obligation', 'Customer-Count', 'Ticket-Average',
           'Net-Sales-Taxable', 'Net-Sales-TaxExempt', 'Net-Sales-Non-Tax', 'Non-Royalty-Amount (-)',
           'Refund-Amount (-)', 'Sales-Tax (+)', 'Occupational-Tax (+)', 'Gross-Sales']
NO_INFORMATION = 'No Information present for this Selection'  # Same reason the Altametrics reader records.


class GatewaySalesError(RuntimeError):
    pass


def missing_secrets(regions=FRANCHISES):
    return [f'{region}_GATEWAY_{part}' for region in regions for part in ('USER', 'PASS')
            if not os.environ.get(f'{region}_GATEWAY_{part}')]


def money(text):
    try:
        return Decimal(text.replace(',', '').replace('$', ''))
    except InvalidOperation:
        raise ValueError('Gateway amount is not a number') from None


def parse_rollup(text, region, day):
    """Store number -> (sales, customers) from the rendered report, with every total checked."""
    franchise = FRANCHISES[region]
    shown = date.fromisoformat(day).strftime('%m/%d/%Y')
    header = re.search(r'\bDate\s+(\d{2}/\d{2}/\d{4})\s+Franchise-#\s+(\d+)', text)
    if not header or header[1] != shown or header[2].lstrip('0') != franchise:
        raise ValueError('Gateway report does not show the requested date and franchise')
    if 'Report-Generated:' not in text or not re.search(r'Page\s*1\s*of\s*1\b', text):
        raise ValueError('Gateway report did not finish rendering on one page')
    cells = [c.strip() for c in re.split(r'\t|\n', text.split(header[0], 1)[1]) if c.strip()]
    if cells[1:1 + len(COLUMNS)] != COLUMNS:
        raise ValueError('Gateway report columns changed')
    cells = cells[1 + len(COLUMNS):]
    if cells and cells[0].lstrip('0') == franchise:
        cells = cells[1:]
    width = len(COLUMNS) - 1
    stores = {}
    while cells and re.fullmatch(r'\d{1,3}', cells[0]):
        row = cells[:width]
        if len(row) != width:
            raise ValueError('Gateway store row is incomplete')
        number = int(row[0])
        if number in stores:
            raise ValueError('Gateway lists a store twice')
        customers = money(row[2])
        if customers != customers.to_integral_value():
            raise ValueError('Gateway customer count is not a whole number')
        stores[number] = (money(row[1]), int(customers))
        cells = cells[width:]
    if not cells or cells[0] != 'Total' or len(cells) < width:
        raise ValueError('Gateway report has no Total row')
    total_sales, total_customers = money(cells[1]), money(cells[2])
    if sum(s for s, _ in stores.values()) != total_sales or sum(c for _, c in stores.values()) != total_customers:
        raise ValueError('Gateway store rows do not add up to the report total')
    return stores


def records_for(region, day, stores, config, allow_missing):
    records = []
    for store in (s['store'] for s in config['stores'] if s['region'] == region):
        number = int(store.split('-')[1])
        if number in stores:
            sales, customers = stores[number]
            records.append({'store': store, 'date': day, 'sales': f'{sales:.2f}',
                            'transactions': customers, 'labor_pct': '0.00'})
        elif allow_missing:
            records.append({'store': store, 'date': day, 'unavailable': NO_INFORMATION})
            print(f'{store}: not in the Gateway roll-up for {day}; recorded as unavailable', flush=True)
        else:
            raise ValueError(f'{store} is missing from the Gateway roll-up for {day}')
    configured = {int(s['store'].split('-')[1]) for s in config['stores'] if s['region'] == region}
    if set(stores) - configured:
        raise ValueError('Gateway lists a store that is not configured; check config.json')
    return records


async def open_report(page, region):
    await page.goto(LOGIN_URL, wait_until='domcontentloaded')
    await page.locator('input[name="username"], input[type="text"]').first.fill(os.environ[f'{region}_GATEWAY_USER'])
    await page.locator('input[type="password"]').first.fill(os.environ[f'{region}_GATEWAY_PASS'])
    await page.locator('button[type="submit"], input[type="submit"], button:has-text("Login")').first.click()
    await page.wait_for_timeout(3000)
    await page.locator('a[href*="Reporting"], a[href*="reporting"], [title*="Reporting"], [aria-label*="Reporting"]').first.click()
    await page.get_by_text(re.compile(r'^\s*' + re.escape(REPORT) + r'\s*$', re.I)).first.click()
    for _ in range(60):
        frame = next((f for f in page.frames if 'paginated-reports.powerbi.com' in f.url), None)
        if frame and await frame.get_by_label('Start Date', exact=True).count() == 1:
            return frame
        await page.wait_for_timeout(1000)
    raise GatewaySalesError(f'{region}: Gateway Franchise Sales Roll-up did not open; check the Gateway login')


async def read_day(page, frame, region, day):
    shown = date.fromisoformat(day).strftime('%m/%d/%Y')
    for label in ('Start Date', 'End Date'):
        field = frame.get_by_label(label, exact=True)
        await field.fill(shown)
        await field.press('Tab')
        if await field.input_value() != shown:
            raise GatewaySalesError(f'{region}: Gateway did not keep the report date')
    dropdown = frame.get_by_label('Franchise Number', exact=True)
    if FRANCHISES[region] not in await dropdown.input_value():
        await dropdown.click()
        option = frame.get_by_role('menuitemcheckbox', name=re.compile(r'0*' + FRANCHISES[region] + r'$'))
        if await option.count() != 1:
            raise GatewaySalesError(f'{region}: Gateway login does not offer franchise {FRANCHISES[region]}')
        if await option.get_attribute('aria-checked') != 'true':
            await option.click()
        await dropdown.press('Escape')
    await frame.get_by_role('button', name=re.compile('View report', re.I)).click()
    problem = None
    for _ in range(90):
        await page.wait_for_timeout(1000)
        try:
            return parse_rollup(await frame.locator('body').inner_text(), region, day)
        except ValueError as exc:
            problem = exc  # Still rendering, or showing the previous date.
    raise GatewaySalesError(f'{region}: {problem}')


async def collect(days, config, allow_missing=False):
    from playwright.async_api import async_playwright
    missing = missing_secrets()
    if missing:
        raise GatewaySalesError('Missing Gateway secrets: ' + ', '.join(missing))
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            async def region_days(region):
                context = await browser.new_context(viewport={'width': 1600, 'height': 1000})
                try:
                    page = await context.new_page()
                    page.set_default_timeout(30000)
                    frame = await open_report(page, region)
                    print(f'{region}: Gateway Franchise Sales Roll-up opened', flush=True)
                    records = []
                    for day in days:
                        rows = records_for(region, day, await read_day(page, frame, region, day), config, allow_missing)
                        folder = Path(__file__).parent / 'reports' / 'source-snapshots' / day
                        folder.mkdir(parents=True, exist_ok=True)
                        (folder / (region + '-gateway.json')).write_text(json.dumps(rows, indent=2), encoding='utf-8')
                        print(f'{region}: {day} Gateway totals verified for {len(rows)} stores', flush=True)
                        records.extend(rows)
                    return records
                finally:
                    await context.close()
            groups = await asyncio.gather(*(region_days(r) for r in FRANCHISES), return_exceptions=True)
        finally:
            await browser.close()
    problems = [str(g) if isinstance(g, (RuntimeError, ValueError)) else type(g).__name__
                for g in groups if isinstance(g, BaseException)]
    if problems:
        raise GatewaySalesError('; '.join(problems))
    return [record for group in groups for record in group]
