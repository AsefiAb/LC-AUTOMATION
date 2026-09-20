"""Read-only regional Altametrics dashboard adapter, based on the original repo.

No password changes, CAPTCHA removal, guessed totals, workbook writes or emails.
"""
import asyncio
import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation

URL = 'https://aplogin.altametrics.com/ers/aplogin/LITTLE_CAESAR.html'
ACCOUNTS = {'CO': ('COLORADO_USERNAME', 'COLORADO_PASSWORD'),
            'AZ': ('AZ_USERNAME', 'AZ_PASSWORD'), 'TX': ('TX_USERNAME', 'TX_PASSWORD')}


def missing_secrets():
    return [key for pair in ACCOUNTS.values() for key in pair if not os.environ.get(key)]


def number(value, percent=False):
    text = str(value).strip().replace(',', '').replace('$', '')
    if percent:
        text = text.removesuffix('%').strip()
    if not re.fullmatch(r'\d+(?:\.\d+)?', text):
        raise ValueError('Altametrics returned a missing or invalid numeric value')
    result = Decimal(text)
    if not result.is_finite():
        raise ValueError('Invalid Altametrics amount')
    return result


def parse_tables(tables):
    candidates = []
    for table in tables:
        for row in table:
            if len(row) < 8 or row[0].strip() != 'Actual Total':
                continue
            # The original dashboard's confirmed Actual Total layout. Require its
            # metric labels too, instead of guessing from number/currency patterns.
            labels = re.sub(r'\s+', ' ', ' '.join(' '.join(r) for r in table)).lower()
            if not all(label in labels for label in ('product net', 'trans', 'punched labor')):
                continue
            try:
                sales, transactions, labor = number(row[1]), number(row[2]), number(row[7], percent=True)
            except ValueError:
                continue  # A header may also say Actual Total; it is not data.
            if transactions != transactions.to_integral_value():
                raise ValueError('Transaction count is not a whole number')
            candidates.append({'sales': f'{sales:.2f}', 'transactions': int(transactions), 'labor_pct': f'{labor:.2f}'})
    if len(candidates) != 1:
        raise ValueError('Expected one unambiguous Actual Total row with the known dashboard columns')
    return candidates[0]


async def guard(page):
    for frame in page.frames:
        if re.search(r'(?:recaptcha|hcaptcha)', frame.url, re.I):
            if await page.locator('iframe[src*="captcha"]:visible').count():
                raise RuntimeError('Altametrics requires interactive verification')
    text = (await page.locator('body').inner_text()).lower()
    if any(s in text for s in ('password expired', 'new password required', 'security check', 'not a robot', 'verify you are human')):
        raise RuntimeError('Altametrics requires an interactive login or password update')


async def settle(page):
    await page.wait_for_timeout(1200)
    try:
        await page.wait_for_load_state('networkidle', timeout=15000)
    except Exception:
        pass
    await guard(page)


async def fill_first(page, selectors, value):
    for selector in selectors:
        loc = page.locator(selector).first
        if await loc.is_visible():
            await loc.fill(value)
            return
    raise RuntimeError('Required Altametrics login field was not found')


async def login(page, region):
    user_key, pass_key = ACCOUNTS[region]
    await page.goto(URL, wait_until='domcontentloaded')
    for _ in range(2):  # Initial login plus the documented Texas SSO redirect.
        await guard(page)
        await fill_first(page, ['input[name="username"]', 'input[name="userId"]', '#validationTooltip01', 'input[name="userName"]', 'input[type="text"]'], os.environ[user_key])
        await fill_first(page, ['input[type="password"]'], os.environ[pass_key])
        await page.locator('button[type="submit"], input[type="submit"], input[value="Login"], button:has-text("Login"), button:has-text("Sign in")').first.click()
        await settle(page)
        if 'ers_webApp' in page.url:
            return
        if '/aim/login/' not in page.url and 'AuthenticationController' not in page.url:
            break
    raise RuntimeError('Altametrics login was not confirmed')


async def date_input(page):
    inputs = page.locator('input:visible')
    matches = []
    for i in range(await inputs.count()):
        if re.fullmatch(r'\d{2}/\d{2}/\d{4}', await inputs.nth(i).input_value()):
            matches.append(inputs.nth(i))
    if len(matches) != 1:
        raise RuntimeError('Altametrics report date input is ambiguous or absent')
    return matches[0]


async def select_date(page, day):
    target = date.fromisoformat(day).strftime('%m/%d/%Y')
    field = await date_input(page)
    await field.fill(target)
    await field.press('Tab')
    # Locate report GO in the date's form, excluding the store-selector GO.
    form = field.locator('xpath=ancestor::form[1]')
    scope = form if await form.count() else page
    buttons = scope.locator('button:visible, input[type="button"]:visible, input[type="submit"]:visible, a:visible')
    candidates = []
    for i in range(await buttons.count()):
        button = buttons.nth(i)
        if await button.get_attribute('id') == 'FILTER_STORE_GO':
            continue
        text = (await button.get_attribute('value') or await button.inner_text()).strip().upper()
        if text == 'GO':
            candidates.append(button)
    if len(candidates) != 1:
        raise RuntimeError('Altametrics report GO button is ambiguous or absent')
    await candidates[0].click()
    await settle(page)
    if await (await date_input(page)).input_value() != target:
        raise RuntimeError('Altametrics did not retain the requested date')


async def select_store(page, store):
    franchise, number_ = store.split('-')
    full = f'0{franchise}-{int(number_):05d}'
    go = page.locator('#FILTER_STORE_GO')
    form = go.locator('xpath=ancestor::form[1]')
    field = form.locator('input.storeSelectorDrop:visible')
    if await field.count() != 1:
        raise RuntimeError('Altametrics store selector is ambiguous')
    await field.fill(f'{full}, {full}')
    await go.click()
    await settle(page)
    field = page.locator('#FILTER_STORE_GO').locator('xpath=ancestor::form[1]').locator('input.storeSelectorDrop:visible')
    selected = set(re.findall(r'\d{4,5}-\d{2,5}', await field.input_value()))
    if selected != {full}:
        raise RuntimeError('Altametrics did not retain the requested single store')


async def collect(day, config):
    if missing_secrets():
        raise RuntimeError('Missing Altametrics secrets: ' + ', '.join(missing_secrets()))
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            async def region_report(region):
                context = await browser.new_context()
                try:
                    page = await context.new_page()
                    page.set_default_timeout(30000)
                    await login(page, region)
                    rows = []
                    for store in (s['store'] for s in config['stores'] if s['region'] == region):
                        await select_store(page, store)
                        await select_date(page, day)
                        selected = await page.locator('#FILTER_STORE_GO').locator('xpath=ancestor::form[1]').locator('input.storeSelectorDrop:visible').input_value()
                        franchise, index = store.split('-')
                        if set(re.findall(r'\d{4,5}-\d{2,5}', selected)) != {f'0{franchise}-{int(index):05d}'}:
                            raise RuntimeError('Report date change reset the store selection')
                        tables = await page.locator('table').evaluate_all('tables => tables.map(t => [...t.rows].map(r => [...r.cells].map(c => c.innerText.trim())))')
                        rows.append({'store': store, 'date': day, **parse_tables(tables)})
                    return rows
                except Exception as exc:
                    raise RuntimeError(f'{region}: Altametrics extraction failed ({type(exc).__name__}); check credentials, challenges and dashboard layout.') from None
                finally:
                    await context.close()
            groups = await asyncio.gather(*(region_report(region) for region in ACCOUNTS))
            return [row for group in groups for row in group]
        finally:
            await browser.close()


def fetch(day, config):
    return asyncio.run(collect(day, config))


def validate(day, records, config):
    by_store = {r['store']: r for r in records}
    if len(by_store) != len(records) or set(by_store) != {s['store'] for s in config['stores']}:
        raise ValueError('Altametrics did not return every configured store exactly once')
    result = []
    for store in config['stores']:
        record = by_store[store['store']]
        if record['date'] != day:
            raise ValueError('Altametrics report date does not match')
        sales, transactions, pct = number(record['sales']), number(record['transactions']), number(record['labor_pct'], True)
        if transactions != transactions.to_integral_value():
            raise ValueError('Transaction count is not integral')
        result.append({**store, 'report_date': day, 'sales': f'{sales:.2f}', 'transactions': int(transactions),
                       'labor_pct': f'{pct:.2f}' if sales else None, 'total_hours': None,
                       'labor_source': 'Altametrics Punched Labor %'})
    return result
