"""Owner-operated capture of Altametrics sales for a date range, one region at a time.

Opens a visible browser; the account owner signs in and completes any security
check themselves. Every configured store is then read for every date in the
range and saved as a private reviewed-source capture. No session, cookie,
credential, workbook write or email is involved. Feed the regional captures to
build_reviewed_source.py to create the payload for the `repair-gaps` workflow.
"""
import argparse
import asyncio
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import altametrics

FOLDER = Path(__file__).resolve().parent
NO_DATA = 'No Actual Total row in Altametrics'


def capture_path(region, start, end):
    return FOLDER / 'reports' / 'reviewed-source' / f'{region}-{start}_{end}.json'


def dates_between(start, end, timezone_name):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or (last - first).days > 30:
        raise ValueError('Capture 1 to 31 consecutive completed dates.')
    if last >= datetime.now(ZoneInfo(timezone_name)).date():
        raise ValueError('Only completed dates before today can be captured.')
    return [(first + timedelta(days=n)).isoformat() for n in range((last - first).days + 1)]


async def read_store(page, store, day):
    """Verified store/date navigation; returns parsed metrics or None for no data."""
    await altametrics.select_store(page, store)
    await altametrics.select_date(page, day)
    field = page.locator('#FILTER_STORE_GO').locator('xpath=ancestor::form[1]').locator('input.storeSelectorDrop:visible')
    franchise, index = store.split('-')
    if set(re.findall(r'\d{4,5}-\d{2,5}', await field.input_value())) != {f'0{franchise}-{int(index):05d}'}:
        raise RuntimeError('Report date change reset the store selection')
    await altametrics.guard(page)
    try:
        return altametrics.parse_tables(await altametrics.report_tables(page))
    except ValueError as exc:
        if 'found 0' in str(exc):
            return None  # No report rows: recorded explicitly, never as zero.
        raise


async def read_region_range(page, region, days, config, reader=read_store):
    """Read every store for every date; a missing report is retried once, then recorded."""
    stores = [s['store'] for s in config['stores'] if s['region'] == region]
    if not stores:
        raise ValueError('No configured stores for region ' + region)
    records, unavailable = [], []
    for day in days:
        for store in stores:
            metrics = await reader(page, store, day)
            if metrics is None:
                metrics = await reader(page, store, day)
            if metrics is None:
                unavailable.append({'date': day, 'store': store, 'reason': NO_DATA})
                print(f'{store} {day}: no report rows; recorded as unavailable', flush=True)
                continue
            records.append({'date': day, 'store': store, 'sales': metrics['sales'],
                            'transactions': int(metrics['transactions'])})
            print(f'{store} {day}: sales and transactions captured', flush=True)
    return records, unavailable


async def capture(region, start, end, config):
    days = dates_between(start, end, config['timezone'])
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={'width': 1600, 'height': 1000})
        try:
            page = await context.new_page()
            page.set_default_timeout(30000)
            await page.goto(altametrics.URL, wait_until='domcontentloaded')
            print(f'{region}: sign in with the {region} account in the opened browser, finish any '
                  'verification, and open the store dashboard. Waiting up to 30 minutes.', flush=True)
            deadline = time.monotonic() + 1800
            last_reason = None
            while True:
                if page.is_closed():
                    raise RuntimeError('The browser was closed before sign-in completed')
                try:
                    await altametrics.verify_account_region(page, region)
                    break
                except Exception as exc:
                    reason = str(exc) if type(exc) is RuntimeError else 'Waiting for the dashboard'
                    if reason != last_reason:
                        print(f'{region}: {reason}', flush=True)
                        last_reason = reason
                    if time.monotonic() > deadline:
                        raise RuntimeError('Sign-in timed out; nothing captured') from None
                    await asyncio.sleep(2)
            print(f'{region}: dashboard confirmed; reading {len(days)} dates.', flush=True)
            records, unavailable = await read_region_range(page, region, days, config)
        finally:
            await context.close()
            await browser.close()
    document = {'version': 1, 'region': region, 'start': start, 'end': end,
                'observed_at': datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
                'system': 'Altametrics', 'report': 'Daily Activity Report',
                'records': records, 'unavailable': unavailable}
    path = capture_path(region, start, end)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding='utf-8')
    print(f'{region}: {len(records)} store/date records and {len(unavailable)} unavailable saved to {path.name}. '
          'No session, workbook or email was touched.', flush=True)
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', choices=sorted(altametrics.ACCOUNTS), required=True)
    parser.add_argument('--start', required=True, help='First completed date, YYYY-MM-DD')
    parser.add_argument('--end', help='Last completed date inclusive; defaults to start')
    args = parser.parse_args()
    config = json.loads((FOLDER / 'config.json').read_text(encoding='utf-8'))
    asyncio.run(capture(args.region, args.start, args.end or args.start, config))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        detail = str(exc) if type(exc) in (RuntimeError, ValueError) else type(exc).__name__
        print('Capture stopped: ' + detail, file=sys.stderr)
        sys.exit(1)
