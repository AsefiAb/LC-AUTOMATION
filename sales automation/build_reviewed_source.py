"""Combine regional reviewed captures into the repair payload, with its exact digest.

The payload is validated with the same checks the GitHub repair uses before
anything is uploaded. `--upload` encrypts it into the SALES_REPAIR_SOURCE_JSON
secret of the private repository; `--dispatch` starts the chosen repair mode
with the matching digest. Nothing here reads Altametrics or sends email.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import requests

import repair_workbooks
from review_sales_source import capture_path, dates_between

FOLDER = Path(__file__).resolve().parent
REGIONS = ('CO', 'TX', 'AZ')
DISPATCH_MODES = ('repair-gaps-preview', 'repair-gaps', 'repair-preview', 'repair', 'preview', 'deliver')
SECRET_LIMIT = 48 * 1024  # GitHub Actions secrets hold at most 48 KB.


def load_captures(start, end, regions=REGIONS):
    captures = []
    for region in regions:
        path = capture_path(region, start, end)
        if not path.exists():
            continue
        document = json.loads(path.read_text(encoding='utf-8'))
        if (document.get('version') != 1 or document.get('region') != region
                or document.get('start') != start or document.get('end') != end):
            raise ValueError(f'{path.name} does not describe {region} {start} to {end}')
        captures.append((path.name, document))
    if not captures:
        raise ValueError(f'No regional captures found for {start} to {end}; run review_sales_source.py first')
    return captures


def build(start, end, reviewed_by, config, regions=REGIONS):
    dates_between(start, end, config['timezone'])
    captures = load_captures(start, end, regions)
    observed = max(datetime.fromisoformat(doc['observed_at'].replace('Z', '+00:00')) for _, doc in captures)
    payload = {'schema_version': 1, 'start': start, 'end': end,
               'regions': [doc['region'] for _, doc in captures],
               'provenance': {'system': 'Altametrics', 'report': 'Daily Activity Report',
                              'sales_metric': 'Product Net',
                              'transactions_metric': 'Actual Total transactions',
                              'method': 'authenticated_browser_review',
                              'observed_at': observed.isoformat(timespec='seconds').replace('+00:00', 'Z'),
                              'reviewed_by': reviewed_by,
                              'evidence': [name for name, _ in captures]},
               'records': [record for _, doc in captures for record in doc['records']],
               'unavailable': [item for _, doc in captures for item in doc.get('unavailable', [])]}
    content = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    if len(content) > SECRET_LIMIT:
        raise ValueError(f'Reviewed source is {len(content):,} bytes; the SALES_REPAIR_SOURCE_JSON secret holds at most '
                         f'{SECRET_LIMIT:,}. Capture a shorter date range or fewer regions and dispatch them separately.')
    digest = hashlib.sha256(content).hexdigest()
    path = FOLDER / 'reports' / 'reviewed-source' / f'reviewed-{start}_{end}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    # The GitHub repair applies exactly these checks before touching OneDrive.
    repair_workbooks.reviewed_source(path, digest, start, end, config)
    return path, digest, payload


def dispatch(secrets, mode, start, end, digest):
    if mode not in DISPATCH_MODES:
        raise ValueError('Unsupported dispatch mode')
    response = requests.post(secrets.url + '/actions/workflows/sales-daily.yml/dispatches',
        headers=secrets.headers, timeout=30,
        # A daily email always covers the last date of the reviewed range.
        json={'ref': 'main', 'inputs': {'report_date': end if mode in ('preview', 'deliver') else start,
                                        'end_date': end, 'mode': mode, 'source_sha256': digest}})
    if response.status_code != 204:
        raise RuntimeError('GitHub did not accept the workflow dispatch (HTTP ' + str(response.status_code) + ')')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--reviewed-by', required=True, help='Who reviewed the captured figures')
    parser.add_argument('--upload', action='store_true', help='Save the payload as the SALES_REPAIR_SOURCE_JSON secret')
    parser.add_argument('--dispatch', choices=DISPATCH_MODES, help='Start this repair mode in GitHub (requires --upload)')
    args = parser.parse_args()
    if args.dispatch and not args.upload:
        raise ValueError('--dispatch requires --upload so the digest matches the stored secret')
    config = json.loads((FOLDER / 'config.json').read_text(encoding='utf-8'))
    path, digest, payload = build(args.start, args.end, args.reviewed_by, config)
    print(f"Reviewed source for {', '.join(payload['regions'])}: {len(payload['records'])} records, "
          f"{len(payload['unavailable'])} unavailable, saved to {path.name}")
    print('SHA-256: ' + digest)
    if args.upload:
        from connect_onedrive import GitHubSecrets
        secrets = GitHubSecrets(allowed_names={'SALES_REPAIR_SOURCE_JSON'})
        secrets.save('SALES_REPAIR_SOURCE_JSON', path.read_text(encoding='utf-8'))
        print('Encrypted payload saved to the SALES_REPAIR_SOURCE_JSON secret.')
        if args.dispatch:
            dispatch(secrets, args.dispatch, args.start, args.end, digest)
            print(f'Started Daily sales report in {args.dispatch} mode for {args.start} to {args.end}; '
                  'check GitHub Actions for the private audit.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        detail = str(exc) if type(exc) in (RuntimeError, ValueError) else type(exc).__name__
        print('Reviewed source build stopped: ' + detail, file=sys.stderr)
        sys.exit(1)
