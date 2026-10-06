"""Read-only regional Altametrics dashboard adapter, based on the original repo.

No password changes, guessed totals, workbook writes or emails.
Cloud compatibility uses the original repository's dashboard overlay handling.
"""
import asyncio
import os
import re
import json
from pathlib import Path
from urllib.parse import urlsplit
from datetime import date
from decimal import Decimal, InvalidOperation
from altametrics_session import REGIONS, alt_host, make_session

URL = 'https://aplogin.altametrics.com/ers/aplogin/LITTLE_CAESAR.html'
ACCOUNTS = {'CO': ('COLORADO_USERNAME', 'COLORADO_PASSWORD'),
            'AZ': ('AZ_USERNAME', 'AZ_PASSWORD'), 'TX': ('TX_USERNAME', 'TX_PASSWORD')}


class AltametricsError(RuntimeError):
    pass


def missing_secrets():
    if os.environ.get('ALTAMETRICS_AUTH_MODE') in ('local-session', 'local-live'):
        import local_sessions
        local_sessions.require_local_windows()
        return []  # Each region's encrypted cache/secret is validated on load.
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
        raise ValueError(f'Expected one unambiguous Actual Total row with the known dashboard columns; found {len(candidates)}')
    return candidates[0]


async def legacy_dashboard_popup(page):
    # The original cloud report removes this client-side dashboard overlay.
    # Restrict that compatibility behavior to the authenticated Altametrics app;
    # never alter a login page, password prompt, or unrelated modal.
    if os.environ.get('ALTAMETRICS_AUTH_MODE') != 'cloud-legacy':
        return
    parsed = urlsplit(page.url)
    if (parsed.scheme == 'https' and alt_host(parsed.hostname)
            and 'ers_webApp' in parsed.path):
        await page.locator('#overlayCap').evaluate_all('els => els.forEach(el => el.remove())')


async def guard(page):
    text = (await page.locator('body').inner_text()).lower()
    if 'there was a problem in completing your request' in text or 'session timeout' in text:
        raise RuntimeError('Altametrics rejected the session; sign in again in this region')
    if any(s in text for s in ('password expired', 'new password required', 'security check', 'not a robot', 'verify you are human')):
        raise RuntimeError('Altametrics requires an interactive login or password update')
    for frame in page.frames:
        if re.search(r'(?:recaptcha|hcaptcha)', frame.url, re.I):
            element = await frame.frame_element()
            if not await element.is_visible():
                continue
            # An attribution badge alone is not a verification challenge.
            if await element.evaluate("el => !!el.closest('.grecaptcha-badge')"):
                continue
            anchor = frame.locator('#recaptcha-anchor')
            if await anchor.count() == 1 and await anchor.get_attribute('aria-checked') == 'true':
                continue
            raise RuntimeError('Altametrics requires interactive verification')
    # Check visible security state before handling any dashboard overlay.
    await legacy_dashboard_popup(page)


async def verify_account_region(page, region):
    parsed = urlsplit(page.url)
    if parsed.scheme != 'https' or not alt_host(parsed.hostname) or 'ers_webApp' not in parsed.path:
        raise RuntimeError('Altametrics session is not on its authenticated dashboard')
    await guard(page)
    field = page.locator('#FILTER_STORE_GO').locator('xpath=ancestor::form[1]').locator('input.storeSelectorDrop:visible')
    if await field.count() != 1:
        raise RuntimeError('Open the Altametrics dashboard store selector to verify the account')
    selected = set(re.findall(r'(\d{4,5})-\d{2,5}', await field.input_value()))
    if not selected or {str(int(s)) for s in selected} != {REGIONS[region]}:
        raise RuntimeError('Altametrics account does not match the requested region')


async def settle(page):
    await page.wait_for_timeout(1200)
    try:
        await page.wait_for_load_state('networkidle', timeout=15000)
    except Exception:
        pass
    await dismiss_popups(page)


async def dismiss_popups(page):
    """Close ordinary notices through their buttons, preserving security prompts."""
    await guard(page)
    dialogs = page.locator('[role="dialog"]:visible, .modal:visible')
    for i in range(await dialogs.count()):
        dialog = dialogs.nth(i)
        buttons = dialog.get_by_role('button', name=re.compile(r'^(Close|Dismiss|Got it|Later|No thanks|Skip)$', re.I))
        if await buttons.count() == 1 and await buttons.first.is_visible():
            await buttons.first.click()
    await guard(page)


async def fill_first(page, selectors, value):
    for selector in selectors:
        loc = page.locator(selector).first
        if await loc.is_visible():
            await loc.fill(value)
            return
    raise RuntimeError('Required Altametrics login field was not found')


async def wait_login_state(page):
    """Let SSO redirects finish and acknowledge the observed supplier notice."""
    for _ in range(60):
        await guard(page)
        host = urlsplit(page.url).hostname
        if not alt_host(host):
            raise RuntimeError('Altametrics login redirected outside its expected hosts')
        if 'ers_webApp' in page.url and await page.locator('#FILTER_STORE_GO:visible').count() == 1:
            await dismiss_popups(page)
            return 'dashboard'
        # This full-page inventory announcement is not a login form. Use its
        # own submit control; never remove overlays or submit security prompts.
        body = (await page.locator('body').inner_text()).lower()
        notice = page.locator('input#Submitbutton[type="submit"]:visible')
        if (host == 'apssopgs.altametrics.com'
                and 'important message regarding: crazy sauce cups/lids' in body
                and await notice.count() == 1):
            await notice.click()
            await page.wait_for_timeout(2000)
            continue
        if await page.locator('input[type="password"]:visible').count() == 1:
            return 'credentials'
        # The SSO page explicitly says it refreshes itself. Wait for the form
        # or dashboard instead of trying to type credentials into that page.
        await page.wait_for_timeout(1000)
    raise RuntimeError('Altametrics login redirect did not finish')


async def login(page, region, session=None):
    if session:
        try:
            await page.goto(session['entry_url'], wait_until='domcontentloaded')
            await settle(page)
            await verify_account_region(page, region)
        except RuntimeError:
            raise RuntimeError(f'{region}: saved session needs interactive reconnection') from None
        return
    user_key, pass_key = ACCOUNTS[region]
    await page.goto(URL, wait_until='load', timeout=60000)
    for attempt in range(3):  # Initial form, SSO form, then confirmation only.
        state = await wait_login_state(page)
        if state == 'dashboard':
            await verify_account_region(page, region)
            return
        if attempt == 2:
            break
        await guard(page)
        await fill_first(page, ['input[name="username"]:visible', 'input[name="userId"]:visible', '#validationTooltip01:visible', 'input[name="userName"]:visible', 'input[type="email"]:visible', 'input[type="text"]:visible'], os.environ[user_key])
        await fill_first(page, ['input[type="password"]:visible'], os.environ[pass_key])
        if (os.environ.get('ALTAMETRICS_AUTH_MODE') == 'cloud-legacy'
                and '/aim/login/' in page.url):
            password = page.locator('input[type="password"]:visible')
            form = password.locator('xpath=ancestor::form[1]')
            if await form.count() != 1:
                raise RuntimeError('SSO login form is ambiguous')
            await form.evaluate('form => HTMLFormElement.prototype.submit.call(form)')
        else:
            await page.locator('button[type="submit"]:visible, input[type="submit"]:visible, input[value="Login"]:visible, input[value="GO"]:visible, button:has-text("Log"):visible, button:has-text("Sign"):visible').first.click()
        await page.wait_for_timeout(8000)
        await settle(page)
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


async def report_tables(page):
    # DataTables renders the report header and totals footer as sibling tables.
    # Keep each widget together, while preventing cross-report header matching.
    return await page.locator('table').evaluate_all('''tables => {
        const groups = [...new Set(tables.map(t => t.closest('.dataTables_wrapper') || t))];
        return groups.map(group => {
            const members = group.tagName === 'TABLE' ? [group] : [...group.querySelectorAll('table')];
            return members.flatMap(t => [...t.rows])
                .filter(r => r.getBoundingClientRect().height > 0 && getComputedStyle(r).visibility !== 'hidden')
                .map(r => [...r.cells].map(c => c.innerText.trim()));
        });
    }''')


async def select_date(page, day):
    target = date.fromisoformat(day).strftime('%m/%d/%Y')
    field = await date_input(page)
    await guard(page)
    # The dashboard date widget needs keyboard events to update its submitted
    # value. Match normal typing; fill() alone can leave its prior date in use.
    await field.click()
    await field.press('Escape')
    await guard(page)
    await field.press('ControlOrMeta+A')
    await field.press_sequentially(target, delay=50)
    await field.press('Tab')
    if await field.input_value() != target:
        raise RuntimeError('Altametrics date widget did not accept the requested date')
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
    observed = await (await date_input(page)).input_value()
    if observed != target:
        raise RuntimeError(f'Altametrics did not retain the requested date: expected {target}, received {observed}')


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
    days = day if isinstance(day, list) else [day]
    if not days or len(set(days)) != len(days):
        raise ValueError('Report dates must be nonempty and unique')
    for value in days:
        date.fromisoformat(value)
    diagnostic_day = days[-1]
    if missing_secrets():
        raise RuntimeError('Missing Altametrics secrets: ' + ', '.join(missing_secrets()))
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        if os.environ.get('ALTAMETRICS_AUTH_MODE') == 'local-live':
            from live_browser import page_for
            rows = []
            for region in ACCOUNTS:
                try:
                    page = await page_for(p, region)
                    await verify_account_region(page, region)
                    rows.extend(await read_region_days(page, region, days, config))
                except Exception as exc:
                    detail = str(exc) if type(exc) in (RuntimeError, ValueError) else type(exc).__name__
                    raise AltametricsError(region + ': dedicated browser needs attention: ' + detail) from None
            return rows
        browser = await p.chromium.launch(headless=True)
        try:
            async def region_report(region):
                session = None
                if os.environ.get('ALTAMETRICS_AUTH_MODE') == 'local-session':
                    import local_sessions
                    session = local_sessions.load(region)
                context = await browser.new_context(viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    **({'storage_state': session['state']} if session else {}))
                stage = 'login'
                try:
                    page = await context.new_page()
                    page.set_default_timeout(30000)
                    for login_attempt in range(3):
                        try:
                            await login(page, region, session)
                            break
                        except RuntimeError as exc:
                            if (session or str(exc) != 'Altametrics login redirect did not finish'
                                    or login_attempt == 2):
                                raise
                            print(f'{region}: SSO redirect stalled; retrying the login entry page', flush=True)
                            await page.wait_for_timeout(2000)
                    print(f'{region}: Altametrics login confirmed', flush=True)
                    def set_stage(value):
                        nonlocal stage
                        stage = value
                    rows = await read_region_days(page, region, days, config, set_stage)
                    if session:
                        local_sessions.save(region, make_session(region, page.url, await context.storage_state()))
                    return rows
                except Exception as exc:
                    # Private diagnostic image masks inputs, including passwords.
                    # Only control metadata (never field values/page text) is saved.
                    try:
                        folder = Path(__file__).parent / 'reports' / ('login-check-' + diagnostic_day)
                        folder.mkdir(parents=True, exist_ok=True)
                        await page.screenshot(path=str(folder / (region + '.png')), mask=[page.locator('input')])
                        meta = {'stage': stage, 'host': urlsplit(page.url).hostname,
                                'dashboard_reached': 'ers_webApp' in page.url,
                                'frame_hosts': [urlsplit(f.url).hostname for f in page.frames],
                                'inputs': await page.locator('input:visible').evaluate_all('els => els.map(e => ({id:e.id,type:e.type}))')}
                        (folder / (region + '.json')).write_text(json.dumps(meta, indent=2), encoding='utf-8')
                        if stage == 'metric parsing':
                            # Inspect only report headers and total rows, never
                            # credentials, cookies, full HTML, or employee rows.
                            layout = await page.locator('table').evaluate_all('''tables => tables.map(t => ({
                                id: t.id, classes: t.className,
                                visible: !!(t.getBoundingClientRect().width && t.getBoundingClientRect().height),
                                parentClasses: t.parentElement?.className,
                                headers: [...t.querySelectorAll('thead tr')].map(r => [...r.cells].map(c => ({text:c.textContent.trim(), rendered:c.innerText.trim()}))),
                                totals: [...t.rows].filter(r => r.cells[0]?.textContent.trim() === 'Actual Total').map(r => [...r.cells].map(c => c.textContent.trim()))
                            })).filter(t => t.totals.length || JSON.stringify(t.headers).includes('Product Net'))''')
                            (folder / (region + '-report-layout.json')).write_text(json.dumps(layout, indent=2), encoding='utf-8')
                    except Exception:
                        pass
                    detail = str(exc) if type(exc) in (RuntimeError, ValueError) else type(exc).__name__
                    raise AltametricsError(f'{region}: {stage} failed: {detail}') from None
                finally:
                    await context.close()
            groups = await asyncio.gather(*(region_report(region) for region in ACCOUNTS), return_exceptions=True)
            failures = [str(g) for g in groups if isinstance(g, AltametricsError)]
            if failures:
                raise AltametricsError('; '.join(failures))
            if any(isinstance(g, BaseException) for g in groups):
                raise AltametricsError('Regional browser session failed')
            return [row for group in groups for row in group]
        finally:
            await browser.close()


def fetch(day, config):
    return asyncio.run(collect(day, config))


def fetch_many(days, config):
    """Read multiple completed dates using one verified login per region."""
    return asyncio.run(collect(list(days), config))


async def read_region_days(page, region, days, config, set_stage=lambda value: None):
    records = []
    for day in days:
        rows = await read_region_report(page, region, day, config, set_stage)
        # Preserve verified source values even if a later region/day or OneDrive
        # fails. No credentials, employee records or session URLs are retained.
        folder = Path(__file__).parent / 'reports' / 'source-snapshots' / day
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (region + '.json')).write_text(json.dumps(rows, indent=2), encoding='utf-8')
        records.extend(rows)
    return records


async def read_region_report(page, region, day, config, set_stage=lambda value: None):
    rows = []
    for store in (s['store'] for s in config['stores'] if s['region'] == region):
        set_stage('store selection')
        await select_store(page, store)
        set_stage('date selection')
        await select_date(page, day)
        selected = await page.locator('#FILTER_STORE_GO').locator('xpath=ancestor::form[1]').locator('input.storeSelectorDrop:visible').input_value()
        franchise, index = store.split('-')
        if set(re.findall(r'\d{4,5}-\d{2,5}', selected)) != {f'0{franchise}-{int(index):05d}'}:
            raise RuntimeError('Report date change reset the store selection')
        set_stage('metric parsing')
        rows.append({'store': store, 'date': day, **parse_tables(await report_tables(page))})
        print(f'{store}: report date and metrics verified', flush=True)
    return rows


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
                       # An open store showing zero labor is not evidence of free
                       # labor: September's detailed reports exposed missing punches.
                       'labor_pct': f'{pct:.2f}' if sales and pct > 0 else None, 'total_hours': None,
                       'labor_status': 'zero_sales' if not sales else ('available' if pct > 0 else 'unverified_zero'),
                       'source_labor_pct': f'{pct:.2f}',
                       'labor_source': 'Altametrics Punched Labor %'})
    return result
