"""Owner-operated Altametrics sign-in; save authorized session as a GitHub secret.

This helper never enters credentials, solves a CAPTCHA, dismisses security
checks, exports unrelated browser sessions, or enables a delivery schedule.
"""
import argparse
import asyncio
import json
import os
import time
from playwright.async_api import async_playwright
from altametrics import URL, verify_account_region
from altametrics_session import secret_name, make_session
from connect_onedrive import GitHubSecrets


async def connect(region):
    name = secret_name(region)
    destination = GitHubSecrets(allowed_names={name})
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        try:
            page = await context.new_page()
            page.set_default_timeout(5000)
            await page.goto(URL, wait_until='domcontentloaded')
            print(f'{region}: sign in in the opened browser and complete any verification. Open the dashboard for this region.', flush=True)
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                if page.is_closed():
                    raise RuntimeError('Sign-in browser was closed before connection completed')
                try:
                    await verify_account_region(page, region)
                except Exception:
                    await asyncio.sleep(2)
                    continue
                state = make_session(region, page.url, await context.storage_state())
                # Prove ordinary session reuse locally before uploading anything.
                probe_context = await browser.new_context(storage_state=state['state'])
                try:
                    probe = await probe_context.new_page()
                    probe.set_default_timeout(10000)
                    await probe.goto(state['entry_url'], wait_until='domcontentloaded')
                    await probe.locator('#FILTER_STORE_GO').wait_for(state='visible', timeout=30000)
                    await verify_account_region(probe, region)
                finally:
                    await probe_context.close()
                destination.save(name, json.dumps(state, separators=(',', ':')))
                print(f'{region}: authorized session reused successfully and saved encrypted to LC-AUTOMATION. No session file written. No email sent.', flush=True)
                return
            raise RuntimeError('Sign-in timed out; no session saved')
        finally:
            await context.close()
            await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', choices=['CO', 'AZ', 'TX'], required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS'):
        raise SystemExit('Run this interactive helper on your own computer')
    try:
        asyncio.run(connect(args.region))
    except Exception as exc:
        print(f'Altametrics connection stopped ({type(exc).__name__}); no completed connection assumed.', flush=True)
        raise SystemExit(1)
