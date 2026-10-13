"""Decide whether a scheduled slot may run. Prints true or false.

Standard library only, so early slots exit before the workflow installs any
dependency. A slot runs when the local time in config['timezone'] is at or
after config['run_time'] (HH:MM, kept constant through daylight saving while
the cron slots stay in UTC) and the report date's delivery receipt does not yet
show both a sent email and a completed catch-up. A receipt read problem never
blocks a delivery attempt; the delivery itself still checks the ledger.
"""
import argparse
import base64
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

FOLDER = Path(__file__).resolve().parent
REPOSITORY = 'AsefiAb/LC-AUTOMATION'


def run_time(config):
    match = re.fullmatch(r'([01]\d|2[0-3]):([0-5]\d)', str(config.get('run_time', '')))
    if not match:
        raise ValueError('config.json run_time must be HH:MM local time')
    return int(match[1]), int(match[2])


def eligible(now, config):
    if now.tzinfo is None:
        raise ValueError('The current time must be timezone-aware')
    local = now.astimezone(ZoneInfo(config['timezone']))
    return (local.hour, local.minute) >= run_time(config)


def report_day(now, config):
    return (now.astimezone(ZoneInfo(config['timezone'])).date() - timedelta(days=1)).isoformat()


def already_complete(day, repository=None, token=None, opener=urllib.request.urlopen):
    """True when the date's receipt records a sent email and a completed catch-up."""
    repository = repository or os.environ.get('GITHUB_REPOSITORY', '')
    token = token or os.environ.get('GITHUB_TOKEN', '')
    if repository != REPOSITORY or not token or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        return False
    request = urllib.request.Request(
        f'https://api.github.com/repos/{repository}/contents/deliveries/{day}.json?ref=sales-delivery-state',
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'})
    try:
        with opener(request, timeout=20) as response:
            payload = json.loads(response.read())
        receipt = json.loads(base64.b64decode(payload['content']))
    except Exception:
        return False  # Missing receipt (HTTP 404) or any read problem: let the run decide.
    return receipt.get('status') == 'sent' and receipt.get('catch_up') == 'complete'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--now', help='ISO 8601 instant with offset, for checks; default is the current time')
    args = parser.parse_args()
    config = json.loads((FOLDER / 'config.json').read_text(encoding='utf-8'))
    now = datetime.fromisoformat(args.now.replace('Z', '+00:00')) if args.now else datetime.now(timezone.utc)
    if not eligible(now, config):
        print('false')
        return 0
    if already_complete(report_day(now, config)):
        print('The report date was delivered and its catch-up completed; this slot has nothing to do.', file=sys.stderr)
        print('false')
        return 0
    print('true')
    return 0


if __name__ == '__main__':
    sys.exit(main())
