"""Private GitHub history and email receipts; browser credentials are never stored here."""
import argparse
import base64
import json
import os
from pathlib import Path
import requests

BRANCH = 'doordash-state'


def request(method, route, **kwargs):
    if os.environ.get('GITHUB_REPOSITORY') != 'AsefiAb/LC-AUTOMATION' or not os.environ.get('GITHUB_TOKEN'):
        raise RuntimeError('DoorDash cloud history requires the private repository token.')
    response = requests.request(method, 'https://api.github.com/repos/AsefiAb/LC-AUTOMATION' + route,
        headers={'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'], 'Accept': 'application/vnd.github+json'},
        timeout=40, **kwargs)
    if response.status_code not in (200, 201):
        raise RuntimeError(f'DoorDash history request failed: HTTP {response.status_code}; do not start with empty history.')
    return response.json()


def data_dir():
    return Path(os.environ['DOORDASH_DATA_DIR'])


def restore():
    remote = request('GET', '/contents/state.json', params={'ref': BRANCH})
    saved = json.loads(base64.b64decode(remote['content']))
    if saved.get('version') != 1 or not isinstance(saved.get('ledger', {}).get('orders'), dict):
        raise ValueError('Invalid DoorDash cloud history.')
    data = data_dir()
    data.mkdir(parents=True, exist_ok=True)
    (data / 'ledger.json').write_text(json.dumps(saved['ledger']), encoding='utf-8')
    for day, receipt in saved.get('email_receipts', {}).items():
        from datetime import date
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError('Invalid receipt date')
        folder = data / 'email-receipts'
        folder.mkdir(exist_ok=True)
        (folder / (day + '.json')).write_text(json.dumps(receipt), encoding='utf-8')
    (data / 'cloud-sha').write_text(remote['sha'], encoding='ascii')


def persist():
    data = data_dir()
    # Compare-and-swap rejects concurrent changes instead of overwriting history.
    sha = (data / 'cloud-sha').read_text(encoding='ascii')
    saved = {'version': 1, 'ledger': json.loads((data / 'ledger.json').read_text(encoding='utf-8')),
             'email_receipts': {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in (data / 'email-receipts').glob('*.json')}}
    result = request('PUT', '/contents/state.json', json={'branch': BRANCH, 'sha': sha,
        'message': 'Checkpoint DoorDash history and delivery receipts',
        'content': base64.b64encode(json.dumps(saved).encode()).decode()})
    (data / 'cloud-sha').write_text(result['content']['sha'], encoding='ascii')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['restore', 'persist'])
    args = parser.parse_args()
    {'restore': restore, 'persist': persist}[args.mode]()
    print('DoorDash cloud history ' + args.mode + ' complete.')
