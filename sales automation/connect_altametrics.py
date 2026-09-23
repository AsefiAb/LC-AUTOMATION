"""Owner-operated Altametrics sign-in; save authorized session as a GitHub secret.

An explicit option uses regional credentials supplied by GitHub to a local
runner. Verification challenges are completed by the account owner.
"""
import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from datetime import date, datetime
from zoneinfo import ZoneInfo
from playwright.async_api import async_playwright
from altametrics import URL, verify_account_region, login, read_region_report, validate
from altametrics_session import secret_name, make_session
from connect_onedrive import GitHubSecrets


async def verify_reusable_session(playwright, region, state):
    """Test the saved login in the same headless mode used by reporting."""
    browser = await playwright.chromium.launch(headless=True)
    try:
        context = await browser.new_context(storage_state=state['state'])
        probe = await context.new_page()
        probe.set_default_timeout(10000)
        await probe.goto(state['entry_url'], wait_until='domcontentloaded')
        await probe.locator('#FILTER_STORE_GO').wait_for(state='visible', timeout=30000)
        await verify_account_region(probe, region)
        # Navigation may rotate cookies. Save the state that actually passed.
        return make_session(region, probe.url, await context.storage_state())
    finally:
        await browser.close()


async def connect(region, credentials_from_env=False, report_date=None):
    name = secret_name(region)
    destination = GitHubSecrets(allowed_names={name})
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        try:
            page = await context.new_page()
            page.set_default_timeout(5000)
            await page.goto(URL, wait_until='domcontentloaded')
            if credentials_from_env:
                print(f'{region}: using regional credentials supplied to this local job.', flush=True)
                try:
                    await login(page, region)
                except Exception:
                    print(f'{region}: finish any sign-in or verification shown in the browser.', flush=True)
            print(f'{region}: sign in in the opened browser and complete any verification. Open the dashboard for this region.', flush=True)
            deadline = time.monotonic() + 1800
            last_reason = None
            while time.monotonic() < deadline:
                if page.is_closed():
                    raise RuntimeError('Sign-in browser was closed before connection completed')
                try:
                    await verify_account_region(page, region)
                except Exception as exc:
                    reason = str(exc) if type(exc) is RuntimeError else 'Waiting for the dashboard to finish loading'
                    if reason != last_reason:
                        print(f'{region}: {reason}', flush=True)
                        last_reason = reason
                    await asyncio.sleep(2)
                    continue
                if report_date:
                    config = json.loads((Path(__file__).parent / 'config.json').read_text(encoding='utf-8'))
                    config['stores'] = [s for s in config['stores'] if s['region'] == region]
                    rows = await read_region_report(page, region, report_date, config)
                    records = validate(report_date, rows, config)
                    folder = Path(__file__).parent / 'reports'
                    folder.mkdir(exist_ok=True)
                    (folder / f'local-{region}-{report_date}.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
                    print(f'{region}: all {len(records)} store reports verified; private report saved. No workbook changes or email.', flush=True)
                state = make_session(region, page.url, await context.storage_state())
                print(f'{region}: dashboard confirmed. Closing sign-in window and testing the saved login.', flush=True)
                break
            else:
                raise RuntimeError('Sign-in timed out; no session saved')
        finally:
            await context.close()
            await browser.close()
        verified = await verify_reusable_session(p, region, state)
        if os.name == 'nt':
            import local_sessions
            local_sessions.save(region, verified)
        destination.save(name, json.dumps(verified, separators=(',', ':')))
        print(f'{region}: login reused successfully after closing the sign-in browser; verified session saved encrypted to LC-AUTOMATION. No email sent.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', choices=['CO', 'AZ', 'TX'], required=True)
    parser.add_argument('--credentials-from-env', action='store_true')
    parser.add_argument('--report-date', type=date.fromisoformat)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') and not (
        os.environ.get('RUNNER_ENVIRONMENT') == 'self-hosted' and os.name == 'nt'
        and os.environ.get('GITHUB_REPOSITORY') == 'AsefiAb/LC-AUTOMATION'
    ):
        raise SystemExit('Run this interactive helper on your own computer or its private local runner')
    if args.report_date and args.report_date >= datetime.now(ZoneInfo('America/Los_Angeles')).date():
        raise SystemExit('Only a completed report date is allowed')
    try:
        asyncio.run(connect(args.region, args.credentials_from_env, args.report_date.isoformat() if args.report_date else None))
    except Exception as exc:
        print(f'Altametrics connection stopped ({type(exc).__name__}); no completed connection assumed.', flush=True)
        raise SystemExit(1)
