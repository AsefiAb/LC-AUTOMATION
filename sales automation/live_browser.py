"""Use three dedicated, owner-signed-in Chrome windows on this computer."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import time

from altametrics_session import alt_host
from local_sessions import require_local_windows
from urllib.parse import urlsplit

PORTS = {'CO': 9331, 'AZ': 9332, 'TX': 9333}


def endpoint(region):
    require_local_windows()
    return 'http://127.0.0.1:' + str(PORTS[region])


def launch(region):
    endpoint(region)
    port = PORTS[region]
    with socket.socket() as connection:
        if connection.connect_ex(('127.0.0.1', port)) == 0:
            return
    chrome = Path(os.environ['ProgramFiles']) / 'Google/Chrome/Application/chrome.exe'
    if not chrome.exists():
        raise RuntimeError('Google Chrome is not installed in its expected location')
    profile = Path(os.environ['LOCALAPPDATA']) / 'LC-Automation' / 'sales-chrome' / region
    from altametrics import URL
    subprocess.Popen([str(chrome), '--remote-debugging-address=127.0.0.1',
        '--remote-debugging-port=' + str(port), '--user-data-dir=' + str(profile),
        '--no-first-run', '--no-default-browser-check', '--new-window', URL],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


async def page_for(playwright, region):
    try:
        browser = await playwright.chromium.connect_over_cdp(endpoint(region), timeout=10000)
    except Exception:
        raise RuntimeError(region + ': open its dedicated sales Chrome window and sign in') from None
    pages = [page for context in browser.contexts for page in context.pages
             if alt_host(urlsplit(page.url).hostname)]
    if len(pages) != 1:
        raise RuntimeError(region + ': keep exactly one Altametrics tab in its dedicated sales window')
    page = pages[0]
    page.set_default_timeout(30000)
    return page


async def connect(region, day):
    from playwright.async_api import async_playwright
    from altametrics import verify_account_region, read_region_report, validate
    launch(region)
    await asyncio.sleep(3)
    config = json.loads((Path(__file__).parent / 'config.json').read_text())
    config['stores'] = [store for store in config['stores'] if store['region'] == region]
    async with async_playwright() as p:
        page = await page_for(p, region)
        print(region + ': sign in and open its store dashboard. Leave this window open afterward.', flush=True)
        deadline = time.monotonic() + 1800
        while True:
            try:
                await verify_account_region(page, region)
                break
            except Exception:
                if page.is_closed() or time.monotonic() > deadline:
                    raise RuntimeError(region + ': sign-in did not complete') from None
                await asyncio.sleep(2)
        rows = await read_region_report(page, region, day, config)
        validate(day, rows, config)
    # Reattach as a new reporting process would. Do not navigate away or close
    # the browser: the working dashboard's server-side form state must survive.
    async with async_playwright() as p:
        page = await page_for(p, region)
        await verify_account_region(page, region)
        rows = await read_region_report(page, region, day, config)
        records = validate(day, rows, config)
        output = Path(__file__).parent / 'reports' / ('live-' + region + '-' + day + '.json')
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(records, indent=2), encoding='utf-8')
        print(f'{region}: all {len(records)} stores passed twice, including a new connection. Keep this browser open. No email sent.', flush=True)


if __name__ == '__main__':
    import argparse
    from datetime import date, datetime, timedelta
    from zoneinfo import ZoneInfo
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--region', choices=PORTS)
    selection.add_argument('--open-all', action='store_true', help='Open the three dedicated sales windows without reading reports')
    parser.add_argument('--report-date', type=date.fromisoformat,
        default=datetime.now(ZoneInfo('America/Los_Angeles')).date() - timedelta(days=1))
    args = parser.parse_args()
    if args.report_date >= datetime.now(ZoneInfo('America/Los_Angeles')).date():
        raise SystemExit('Use a completed report date')
    try:
        if args.open_all:
            for region in PORTS:
                launch(region)
            print('Sales browsers opened. Sign into any region that asks, and leave all three windows open.')
        else:
            asyncio.run(connect(args.region, args.report_date.isoformat()))
    except Exception as exc:
        print('Sales browser check stopped: ' + (str(exc) if type(exc) is RuntimeError else type(exc).__name__), flush=True)
        raise SystemExit(1)
