"""Gateway Financial View tip collection ported from AsefiAb/tips-yolo.

Keep that repository's two tip fields and per-store report totals. The payroll
adapter additionally rejects missing/duplicate data and verifies dates/stores.
"""
import asyncio
import os
import re
import json
from pathlib import Path
from urllib.parse import urlsplit
from datetime import date, datetime, timezone
from decimal import Decimal

REGIONS = {'CO': ('3356', 7), 'AZ': ('3759', 7), 'TX': ('3910', 4)}
LABELS = ('InStore Tips (+)', 'Prepaid InStore Tips (+)')
REFERENCE_REPOSITORY = 'https://github.com/AsefiAb/tips-yolo'
REFERENCE_REVISION = 'f28a2698e41dee97df2e673032e34fe06450983d'


class GatewayError(RuntimeError):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def transient_report_error(exc, stage):
    """Retry transport/rendering only; never retry failed source validation."""
    if stage not in ('browser context', 'report navigation', 'Financial View menu',
                     'Financial View frame', 'report refresh'):
        return False
    kind = type(exc)
    is_playwright = kind.__module__.startswith('playwright.')
    if isinstance(exc, TimeoutError) or (is_playwright and kind.__name__ == 'TimeoutError'):
        return True
    return is_playwright and any(label in str(exc) for label in (
        'net::ERR_CONNECTION_RESET', 'net::ERR_CONNECTION_CLOSED', 'net::ERR_TIMED_OUT',
        'net::ERR_NETWORK_CHANGED', 'net::ERR_HTTP2_PROTOCOL_ERROR'))


async def retry_region(region, run, attempts=2):
    for attempt in range(1, attempts + 1):
        try:
            return await run(attempt)
        except GatewayError as exc:
            if not exc.retryable or attempt == attempts:
                raise
            print(f'{region}: transient Gateway report failure; retrying with a fresh session ({attempt + 1}/{attempts})', flush=True)
            await asyncio.sleep(5)


def missing_secrets(regions=None):
    return [f'{region}_GATEWAY_{suffix}' for region in (REGIONS if regions is None else regions) for suffix in ('USER', 'PASS')
            if not os.environ.get(f'{region}_GATEWAY_{suffix}')]


def selected_stores(stores=None):
    available = {f'{franchise}-{i:02d}': (region, i)
                 for region, (franchise, count) in REGIONS.items() for i in range(1, count + 1)}
    if stores is None:
        stores = list(available)
    if (not isinstance(stores, (list, tuple)) or not stores or len(set(stores)) != len(stores)
            or any(s not in available for s in stores)):
        raise ValueError('Gateway store filter must contain unique configured stores such as 3356-03')
    return {region: [available[s][1] for s in stores if available[s][0] == region]
            for region in REGIONS if any(available[s][0] == region for s in stores)}


def financial_evidence(store, dates, parameters, tables, visible_text):
    """Rendered report proof, not merely selected controls; no sales inference."""
    lines = [line.strip() for line in visible_text.splitlines()
             if not line.strip().lower().startswith('retrieved:')]
    report_text = '\n'.join(lines)
    franchises = {franchise for franchise, _ in REGIONS.values()}
    rendered_stores = sorted({f'{int(franchise)}-{int(number):02d}'
        for franchise, number in re.findall(r'(?<!\d)(\d{4,5})-(\d{2,5})(?!\d)', report_text)
        if str(int(franchise)) in franchises})
    rendered_dates = sorted(set(re.findall(r'\b\d{4}-\d{2}-\d{2}\b', report_text)))
    first, last = dates['start_date'], dates['end_date']
    checks = {'rendered_store_matches': rendered_stores == [store],
              'rendered_dates_match': bool(rendered_dates) and first in rendered_dates and last in rendered_dates
                                      and all(first <= d <= last for d in rendered_dates),
              'start_control_matches': parameters['start'] == date.fromisoformat(first).strftime('%m/%d/%Y'),
              'end_control_matches': parameters['end'] == date.fromisoformat(last).strftime('%m/%d/%Y'),
              'store_control_matches': set(re.findall(r'\b\d{5}\b', parameters['store'])) == {store.split('-')[1].zfill(5)}}
    return {'store': store, 'period': dates, 'captured_at': datetime.now(timezone.utc).isoformat(),
            'report': 'Gateway Financial View', 'source_verified': all(checks.values()), 'checks': checks,
            'parameters': parameters, 'rendered_store_ids': rendered_stores, 'rendered_dates': rendered_dates,
            'visible_text': visible_text, 'tables': tables}


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
            # Reversals appear as -4.29 or (4.29); they reduce the store pool as reported.
            accounting = re.fullmatch(r'\((\d+(?:\.\d{1,2})?)\)', value)
            if accounting:
                value = '-' + accounting[1]
            if not re.fullmatch(r'-?\d+(?:\.\d{1,2})?', value):
                # Name the row and show the cell (a dollar amount, never credentials) so the
                # cause is visible in the run log; the value is still never guessed.
                shown = cells[-2].strip()[:24] or '(blank)'
                raise ValueError(f'Gateway tip amount is missing or invalid: {label} = {shown}; row cells {len(cells)}')
            found[label] = Decimal(value)
    if set(found) != set(LABELS):
        raise ValueError('Both Gateway tip lines must be present; blanks are not zero')
    if sum(found.values()) < 0:
        raise ValueError('Gateway store tip total is negative; review the reversals before payroll')
    return {'instore_tips': f'{found[LABELS[0]]:.2f}', 'prepaid_instore_tips': f'{found[LABELS[1]]:.2f}',
            'total_tips': f'{sum(found.values()):.2f}'}


async def collect(dates, *, stores=None, capture_financial=False, diagnostic_folder=None):
    selection = selected_stores(stores)
    missing = missing_secrets(selection)
    if missing:
        raise RuntimeError('Missing Gateway secrets: ' + ', '.join(missing))
    from playwright.async_api import async_playwright
    start = date.fromisoformat(dates['start_date']).strftime('%m/%d/%Y')
    stop = date.fromisoformat(dates['end_date']).strftime('%m/%d/%Y')
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            async def region_report(region, franchise, count, attempt):
                context, page = None, None
                stage = 'browser context'
                try:
                    context = await browser.new_context()
                    page = await context.new_page()
                    page.set_default_timeout(30000)
                    stage = 'login'
                    await page.goto('https://www.lcegateway.com/login', wait_until='domcontentloaded')
                    await page.locator('input[name="username"], input[type="text"]').first.fill(os.environ[f'{region}_GATEWAY_USER'])
                    await page.locator('input[type="password"]').first.fill(os.environ[f'{region}_GATEWAY_PASS'])
                    await page.locator('button[type="submit"], input[type="submit"], button:has-text("Login")').first.click()
                    await page.wait_for_timeout(2000)
                    stage = 'report navigation'
                    # Follow the authenticated site's actual reporting route;
                    # the old fallback /Reporting URL now returns page-not-found.
                    await page.locator('a[href*="Reporting"], a[href*="reporting"], [title*="Reporting"], [aria-label*="Reporting"]').first.click()
                    stage = 'Financial View menu'
                    await page.get_by_text(re.compile(r'^\s*Financial\s+View\s*$', re.I)).first.click()
                    stage = 'Financial View frame'
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
                    # The Power BI frame exists before its parameter controls.
                    # Wait for initial rendering before deciding to toggle the panel.
                    try:
                        await frame.locator('#datePicker-input4').wait_for(state='visible', timeout=30000)
                    except Exception:
                        pass
                    result = []
                    for index in selection[region]:
                        stage = 'date parameters'
                        if not await frame.locator('#datePicker-input4').is_visible():
                            await frame.locator('[title*="Parameters"], button:has-text("Parameters")').first.click()
                        for selector, value in (('#datePicker-input4', start), ('#datePicker-input9', stop)):
                            await frame.locator(selector).fill(value)
                            await frame.locator(selector).press('Tab')
                            if await frame.locator(selector).input_value() != value:
                                raise RuntimeError('Gateway report date did not match')
                        dropdown = frame.get_by_label('Franchise Store', exact=True)
                        stage = 'store selection'
                        await dropdown.click()
                        # Read the checkbox labels/state, not fixed screen coordinates.
                        target = f'{index:05d}'
                        await frame.get_by_role('menuitemcheckbox').first.wait_for(state='visible')
                        for number in range(1, count + 1):
                            label = f'{number:05d}'
                            option = frame.get_by_role('menuitemcheckbox', name=label, exact=True)
                            if await option.count() != 1:
                                raise RuntimeError('Gateway store checkbox labels are ambiguous or missing')
                            desired = 'true' if label == target else 'false'
                            if await option.get_attribute('aria-checked') != desired:
                                await option.click()
                            if await option.get_attribute('aria-checked') != desired:
                                raise RuntimeError('Gateway store checkbox did not retain selection')
                        await dropdown.press('Escape')
                        selected = set(re.findall(r'\b\d{5}\b', await dropdown.input_value()))
                        if selected != {target}:
                            raise RuntimeError('Gateway selection is not exactly one requested store')
                        stage = 'report refresh'
                        old_report = await frame.locator('table').evaluate_all('tables => tables.filter(t => t.innerText.includes("InStore Tips (+)")).map(t => t.innerText).join("\\n")')
                        if capture_financial:
                            old_report = await frame.locator('table').evaluate_all('tables => tables.filter(t => t.innerText.includes("Financial View")).map(t => t.innerText).join("\\n")')
                        await frame.locator('button:has-text("View Report"), button:has-text("View report"), input[value="View Report"]').first.click()
                        try:
                            await frame.get_by_text('Loading report', exact=False).wait_for(state='visible', timeout=5000)
                        except Exception:
                            pass
                        await frame.get_by_text('Loading report', exact=False).wait_for(state='hidden', timeout=60000)
                        if capture_financial:
                            await frame.get_by_text(re.compile(r'Sales Entry Form\s*-\s*Financial View', re.I)).first.wait_for()
                        else:
                            await frame.get_by_text(LABELS[0], exact=True).first.wait_for()
                        await page.wait_for_timeout(1000)
                        if old_report:
                            if capture_financial:
                                await frame.wait_for_function('old => {const text=[...document.querySelectorAll("table")].filter(t=>t.innerText.includes("Financial View")).map(t=>t.innerText).join("\\n"); return text && text!==old}', arg=old_report, timeout=30000)
                            else:
                                await frame.wait_for_function('old => {const text=[...document.querySelectorAll("table")].filter(t=>t.innerText.includes("InStore Tips (+)")).map(t=>t.innerText).join("\\n"); return text && text!==old}', arg=old_report, timeout=30000)
                        if await frame.locator('#datePicker-input4').input_value() != start or await frame.locator('#datePicker-input9').input_value() != stop:
                            raise RuntimeError('Gateway report dates changed during refresh')
                        if set(re.findall(r'\b\d{5}\b', await dropdown.input_value())) != {target}:
                            raise RuntimeError('Gateway store changed during refresh')
                        tables = await frame.locator('table').evaluate_all('tables => tables.map(t => [...t.rows].map(r => [...r.cells].map(c => c.innerText.trim())))')
                        if capture_financial:
                            stage = 'financial source capture'
                            raw_tables = await frame.locator('table').evaluate_all('''tables => tables
                                .filter(t => t.getClientRects().length && getComputedStyle(t).visibility !== "hidden")
                                .map(t => ({caption:t.caption?.innerText || "", rows:[...t.rows]
                                    .filter(r=>r.getClientRects().length).map(r=>({cells:[...r.cells]
                                    .map(c=>({tag:c.tagName,text:c.innerText,row_span:c.rowSpan,col_span:c.colSpan}))}))}))''')
                            rendered_text = '\n'.join('\n'.join(c['text'] for c in row['cells'])
                                for table in raw_tables for row in table['rows'])
                            parameters = {'start': await frame.locator('#datePicker-input4').input_value(),
                                          'end': await frame.locator('#datePicker-input9').input_value(),
                                          'store': await dropdown.input_value()}
                            evidence = financial_evidence(f'{franchise}-{index:02d}', dates, parameters,
                                                          raw_tables, rendered_text)
                            if diagnostic_folder:
                                diagnostic_folder.mkdir(parents=True, exist_ok=True)
                                filename = f'{franchise}-{index:02d}-Financial-View'
                                # Persist text before screenshot attempts, without HTML/auth tokens.
                                (diagnostic_folder / (filename + '.json')).write_text(json.dumps(evidence, indent=2), encoding='utf-8')
                                try:
                                    await frame.locator('body').screenshot(path=str(diagnostic_folder / (filename + '.png')), timeout=10000)
                                except Exception:
                                    pass
                            result.append(evidence)
                            continue
                        stage = 'tip parsing'
                        try:
                            parsed = parse_tip_rows(tables)
                        except ValueError as exc:
                            raise ValueError(f'{franchise}-{index:02d}: {exc}') from None
                        for key in ('instore_tips', 'prepaid_instore_tips'):
                            if Decimal(parsed[key]) < 0:
                                print(f'{franchise}-{index:02d}: Gateway {key} is {parsed[key]} (reversal); '
                                      f'store tip pool is {parsed["total_tips"]}', flush=True)
                        result.append({'store': f'{franchise}-{index:02d}', **parsed})
                    return result
                except Exception as exc:
                    retryable = transient_report_error(exc, stage)
                    # Interactive/security pages need the account owner, even
                    # if waiting for a report link happened to time out.
                    if retryable and page is not None:
                        try:
                            if re.search(r'/login(?:/|$)', urlsplit(page.url).path, re.I):
                                retryable = False
                            challenges = page.get_by_text(re.compile(
                                r'captcha|security check|verify your identity|verification code|two.factor', re.I))
                            for element in await challenges.all():
                                if await element.is_visible():
                                    retryable = False
                            for element in await page.locator('iframe[src*="captcha" i], iframe[title*="challenge" i]').all():
                                if await element.is_visible():
                                    retryable = False
                        except Exception:
                            retryable = False
                    detail = str(exc) if type(exc) in (RuntimeError, ValueError) else type(exc).__name__
                    message = f'{region}: Gateway {stage} failed: {detail}'
                    folder = ((diagnostic_folder / 'errors') if diagnostic_folder else
                              Path(__file__).parent / 'reports' / (dates['start_date'] + '_' + dates['end_date']) / 'gateway-diagnostics')
                    folder.mkdir(parents=True, exist_ok=True)
                    diagnostic = region + '-attempt-' + str(attempt)
                    metadata = {'region': region, 'stage': stage, 'attempt': attempt,
                                'error': message, 'retryable': retryable}
                    # Write the cause first. Screenshots/DOM inspection can fail
                    # on a disconnected page and must not erase the diagnosis.
                    (folder / (diagnostic + '.json')).write_text(json.dumps(metadata, indent=2), encoding='utf-8')
                    try:
                        await page.screenshot(path=str(folder / (diagnostic + '.png')), mask=[page.locator('input')], timeout=10000)
                        metadata.update({'host': urlsplit(page.url).hostname,
                                    'frame_hosts': [urlsplit(f.url).hostname for f in page.frames],
                                    'report_link_labels': await page.locator('a').all_text_contents(),
                                    'inputs': await page.locator('input:visible').evaluate_all('els=>els.map(e=>({id:e.id,type:e.type}))')})
                        metadata['report_controls'] = []
                        for report_frame in page.frames:
                            if 'paginated-reports.powerbi.com' in report_frame.url:
                                metadata['report_controls'] += await report_frame.locator('input').evaluate_all('els=>els.slice(0,100).map(e=>({id:e.id,type:e.type,label:e.getAttribute("aria-label"),visible:!!(e.offsetWidth||e.offsetHeight)}))')
                                metadata['checkboxes'] = await report_frame.get_by_role('menuitemcheckbox').evaluate_all('els=>els.map(e=>({text:e.textContent,checked:e.getAttribute("aria-checked")}))')
                                metadata['store_options'] = await report_frame.get_by_text('00001', exact=True).evaluate_all('els=>els.map(e=>({html:e.parentElement.outerHTML.slice(0,8000)}))')
                        (folder / (diagnostic + '.json')).write_text(json.dumps(metadata, indent=2), encoding='utf-8')
                    except Exception:
                        pass
                    raise GatewayError(message, retryable=retryable) from None
                finally:
                    if context is not None:
                        try:
                            await context.close()
                        except Exception:
                            pass
            groups = await asyncio.gather(*(retry_region(region,
                lambda attempt, r=region, d=definition: region_report(r, *d, attempt))
                for region, definition in REGIONS.items() if region in selection), return_exceptions=True)
            failures = [str(g) for g in groups if isinstance(g, GatewayError)]
            if failures:
                raise GatewayError('; '.join(failures))
            if any(isinstance(g, BaseException) for g in groups):
                raise GatewayError('Gateway browser session failed')
            return {'verified': not capture_financial, 'issues': [], 'period': dates,
                    **({'purpose': 'Read-only financial source capture; not a payroll input'} if capture_financial else {}),
                    'source': {'system': 'LCE Gateway', 'report': 'Financial View',
                               'reference_repository': REFERENCE_REPOSITORY,
                               'reference_revision': REFERENCE_REVISION,
                               'tip_fields': list(LABELS)},
                    'stores': [r for group in groups for r in group]}
        finally:
            await browser.close()


def fetch(dates):
    return asyncio.run(collect(dates))
