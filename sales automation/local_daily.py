"""Daily sales from this PC's signed-in Chrome profiles (run by Windows Task Scheduler).

Altametrics challenges GitHub-hosted logins. Each region keeps its own Chrome
profile under %LOCALAPPDATA% (never OneDrive), so cookies survive restarts and
sleep. The PC reads every store with the same checked reader as an owner
capture; GitHub then checks the digest, fills the workbooks and sends the email
exactly as for a reviewed source. Nothing here writes workbooks or sends email.

  python local_daily.py               run now (what the scheduled task does)
  python local_daily.py --sign-in     open the three profiles once and sign in
  python local_daily.py --save-login CO   store that region's sign-in for automatic re-login
  python local_daily.py --install     register the 7:00 AM wake-from-sleep task
"""
import argparse
import asyncio
import getpass
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

FOLDER = Path(__file__).resolve().parent
TASK = 'LC Automation - Daily sales'
REGIONS = ('CO', 'AZ', 'TX')
NAMES = {'CO': 'Colorado', 'AZ': 'Arizona', 'TX': 'Texas'}
MAX_DAYS = 7          # Catch-up window when the PC missed mornings.
SIGN_IN_WAIT = 25 * 60  # The task repeats every 30 minutes until noon.
REVIEWED_BY = "This PC's signed-in Altametrics browsers (automated daily run)"


def home():
    from local_sessions import require_local_windows
    require_local_windows()
    return Path(os.environ['LOCALAPPDATA']) / 'LC-Automation'


def profile(region):
    return home() / 'sales-chrome' / region


def login_path(region):
    if region not in REGIONS:
        raise ValueError('Unknown region')
    return home() / 'sales-logins' / (region + '.bin')


def save_login(region):
    from local_sessions import crypt
    print(f'{NAMES[region]} Altametrics sign-in. It is encrypted for this Windows user only.')
    username = input('Username: ').strip()
    password = getpass.getpass('Password (not shown): ')
    if not username or not password:
        raise SystemExit('Nothing saved.')
    path = login_path(region)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(crypt(json.dumps({'username': username, 'password': password}).encode()))
    print(f'Saved. {NAMES[region]} will sign itself back in when its cookies expire.')


def load_login(region):
    from local_sessions import crypt
    path = login_path(region)
    if not path.exists():
        return None
    try:
        return json.loads(crypt(path.read_bytes(), decrypt=True))
    except Exception:
        return None


def pending_days(today, sent):
    """Unsent completed dates ending yesterday, oldest first; empty when yesterday was sent."""
    days, day = [], today - timedelta(days=1)
    while len(days) < MAX_DAYS and not sent(day.isoformat()):
        days.insert(0, day.isoformat())
        day -= timedelta(days=1)
    return days


class GitHub:
    def __init__(self):
        from connect_onedrive import GitHubSecrets
        self.secrets = GitHubSecrets(allowed_names={'SALES_REPAIR_SOURCE_JSON'})
        self.url, self.headers = self.secrets.url, self.secrets.headers

    def receipt(self, day):
        response = requests.get(f'{self.url}/contents/deliveries/{day}.json', headers=self.headers,
                                params={'ref': 'sales-delivery-state'}, timeout=30)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        import base64
        return json.loads(base64.b64decode(response.json()['content']))

    def sent(self, day):
        return (self.receipt(day) or {}).get('status') == 'sent'

    def run_and_wait(self, mode, start, end, digest):
        from build_reviewed_source import dispatch
        started = datetime.now(timezone.utc) - timedelta(seconds=5)
        dispatch(self.secrets, mode, start, end, digest)
        run = None
        for _ in range(180):  # Up to an hour.
            time.sleep(20)
            if run is None:
                runs = requests.get(f'{self.url}/actions/workflows/sales-daily.yml/runs', headers=self.headers,
                                    params={'per_page': 5, 'event': 'workflow_dispatch'}, timeout=30).json()['workflow_runs']
                run = next((r for r in runs if datetime.fromisoformat(r['created_at'].replace('Z', '+00:00')) >= started), None)
                if run is None:
                    continue
            run = requests.get(f"{self.url}/actions/runs/{run['id']}", headers=self.headers, timeout=30).json()
            if run['status'] == 'completed':
                log(f"GitHub {mode} run {run['html_url']} finished: {run['conclusion']}")
                return run['conclusion'] == 'success'
        raise RuntimeError(f'GitHub {mode} run did not finish within an hour')


def log(text):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {text}"
    print(line, flush=True)
    try:
        folder = home() / 'logs'
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / f"sales-{date.today().isoformat()}.log", 'a', encoding='utf-8') as handle:
            handle.write(line + '\n')
    except Exception:
        pass


async def banner(page, text):
    try:
        await page.evaluate('''text => {
            let b = document.getElementById('lc-sales-banner');
            if (!b) { b = document.createElement('div'); b.id = 'lc-sales-banner';
              b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#0b5;color:#fff;font:bold 18px sans-serif;padding:8px 14px;text-align:center';
              document.body.appendChild(b); }
            b.textContent = text; }''', text)
    except Exception:
        pass


async def signed_in_page(playwright, region):
    """This region's own Chrome profile on its verified dashboard."""
    import altametrics
    context = await playwright.chromium.launch_persistent_context(
        str(profile(region)), channel='chrome', headless=False, viewport={'width': 1400, 'height': 900},
        args=['--no-first-run', '--no-default-browser-check'])
    page = context.pages[0] if context.pages else await context.new_page()
    page.set_default_timeout(30000)
    try:
        await page.goto(altametrics.URL, wait_until='load', timeout=60000)
        if await altametrics.wait_login_state(page) == 'dashboard':
            await altametrics.verify_account_region(page, region)
            log(f'{region}: still signed in from saved cookies')
            return context, page
        saved = load_login(region)
        if saved:
            user_key, pass_key = altametrics.ACCOUNTS[region]
            os.environ[user_key], os.environ[pass_key] = saved['username'], saved['password']
            try:
                await altametrics.login(page, region)
                log(f'{region}: signed back in with the saved login')
                return context, page
            except RuntimeError as exc:
                log(f'{region}: automatic sign-in stopped ({exc}); waiting for the owner')
            finally:
                os.environ.pop(user_key, None)
                os.environ.pop(pass_key, None)
    except RuntimeError as exc:
        log(f'{region}: {exc}; waiting for the owner')
    # A CAPTCHA, password change or missing saved login is the owner's to do.
    log(f'{region}: sign in to {NAMES[region]} in the window that opened (waiting {SIGN_IN_WAIT // 60} minutes)')
    deadline = time.monotonic() + SIGN_IN_WAIT
    while time.monotonic() < deadline:
        # Signing in can open the dashboard in a new tab and close the first one.
        pages = [candidate for candidate in context.pages if not candidate.is_closed()]
        if not pages:
            break
        for candidate in pages:
            try:
                await altametrics.verify_account_region(candidate, region)
                candidate.set_default_timeout(30000)
                log(f'{region}: owner signed in')
                return context, candidate
            except Exception:
                await banner(candidate, f'LC Automation: please sign in to {NAMES[region]} here. Daily sales continues by itself.')
        await asyncio.sleep(3)
    await context.close()
    raise RuntimeError(f'{region}: not signed in')


async def capture(days, config):
    from playwright.async_api import async_playwright
    from review_sales_source import capture_path, read_region_range

    async def one(p, region):
        context, page = await signed_in_page(p, region)
        try:
            await banner(page, f'LC Automation: reading {NAMES[region]} sales. This window closes by itself.')
            records, unavailable = await read_region_range(page, region, days, config)
        finally:
            await context.close()  # Cookies stay in the profile.
        path = capture_path(region, days[0], days[-1])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            'version': 1, 'region': region, 'start': days[0], 'end': days[-1],
            'observed_at': datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
            'system': 'Altametrics', 'report': 'Daily Activity Report',
            'records': records, 'unavailable': unavailable}, indent=2), encoding='utf-8')
        log(f'{region}: {len(records)} store-days read, {len(unavailable)} with no Altametrics report')

    async with async_playwright() as p:
        results = await asyncio.gather(*(one(p, region) for region in REGIONS), return_exceptions=True)
    problems = [str(r) if isinstance(r, RuntimeError) else type(r).__name__ for r in results if isinstance(r, BaseException)]
    if problems:
        raise RuntimeError('; '.join(problems))


async def first_sign_in(regions=REGIONS):
    """Open each profile until the owner signs in, then say whether cookies outlive a restart."""
    from playwright.async_api import async_playwright
    from altametrics_session import alt_host

    async def one(p, region):
        context, page = await signed_in_page(p, region)
        try:
            await banner(page, f'{NAMES[region]} is connected. This window closes by itself.')
            cookies = [c for c in await context.cookies() if alt_host(c['domain'].lstrip('.'))]
            lasting = [c['expires'] for c in cookies if c['expires'] > time.time() + 86400]
            log(f'{region}: signed in; {len(lasting)} of {len(cookies)} Altametrics cookies last past tomorrow'
                + (f" (longest until {datetime.fromtimestamp(max(lasting)):%Y-%m-%d})" if lasting else ''))
        finally:
            await context.close()

    async with async_playwright() as p:
        results = await asyncio.gather(*(one(p, region) for region in regions), return_exceptions=True)
    problems = [str(r) if isinstance(r, RuntimeError) else type(r).__name__ for r in results if isinstance(r, BaseException)]
    if problems:
        raise RuntimeError('; '.join(problems))
    return 0


def run():
    from build_reviewed_source import build
    from review_sales_source import dates_between
    config = json.loads((FOLDER / 'config.json').read_text(encoding='utf-8'))
    github = GitHub()
    today = datetime.now(ZoneInfo(config['timezone'])).date()
    days = pending_days(today, github.sent)
    if not days:
        log(f'{(today - timedelta(days=1)).isoformat()} was already sent; nothing to do')
        return 0
    days = dates_between(days[0], days[-1], config['timezone'])
    log(f'Reading Altametrics for {days[0]} to {days[-1]}')
    asyncio.run(capture(days, config))
    path, digest, payload = build(days[0], days[-1], REVIEWED_BY, config)
    github.secrets.save('SALES_REPAIR_SOURCE_JSON', path.read_text(encoding='utf-8'))
    log(f"Checked source saved: {len(payload['records'])} records, {len(payload['unavailable'])} unavailable")
    if len(days) > 1 and not github.run_and_wait('repair-gaps', days[0], days[-1], digest):
        raise RuntimeError('Filling the earlier missed days failed; see GitHub Actions')
    if not github.run_and_wait('deliver', days[0], days[-1], digest) or not github.sent(days[-1]):
        raise RuntimeError(f'The {days[-1]} email was not confirmed; see GitHub Actions')
    log(f'Daily sales email for {days[-1]} sent')
    return 0


INSTALL = r'''
$ErrorActionPreference = 'Stop'
$action = New-ScheduledTaskAction -Execute '{pythonw}' -Argument '"{script}"' -WorkingDirectory '{folder}'
$trigger = New-ScheduledTaskTrigger -Daily -At 7:00am
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 7:00am -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Hours 5)).Repetition
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RunOnlyIfNetworkAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName '{task}' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Reads Altametrics with the saved Chrome profiles and has GitHub send the daily sales email.' -Force | Out-Null
"Registered '{task}'"
'''


def install():
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    script = INSTALL.format(pythonw=pythonw, script=Path(__file__).resolve(), folder=FOLDER, task=TASK)
    subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--save-login', choices=REGIONS)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--sign-in', nargs='*', choices=REGIONS, help='Regions to sign in (default: all)')
    args = parser.parse_args()
    if args.sign_in is not None:
        return asyncio.run(first_sign_in(args.sign_in or REGIONS))
    if args.save_login:
        return save_login(args.save_login)
    if args.install:
        return install()
    return run()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        log('Daily sales stopped: ' + (str(exc) if type(exc) in (RuntimeError, ValueError) else type(exc).__name__))
        sys.exit(1)
