"""One-time local Microsoft sign-in. Never run this helper in GitHub Actions."""
import argparse
import json
import os
import time
from pathlib import Path
import requests


def main():
    if os.environ.get('GITHUB_ACTIONS'):
        raise SystemExit('Run the OneDrive sign-in helper locally, not in Actions.')
    parser = argparse.ArgumentParser()
    parser.add_argument('--client-id', required=True, help='Your Microsoft public-client application ID')
    args = parser.parse_args()
    base = 'https://login.microsoftonline.com/common/oauth2/v2.0/'
    response = requests.post(base + 'devicecode', data={
        'client_id': args.client_id,
        'scope': 'https://graph.microsoft.com/Files.ReadWrite offline_access'}, timeout=30)
    if not response.ok:
        raise SystemExit('Microsoft rejected device sign-in. Check the app ID and public-client setup.')
    device = response.json()
    print(device['message'])
    deadline = time.monotonic() + device['expires_in']
    interval = max(5, device.get('interval', 5))
    while time.monotonic() < deadline:
        time.sleep(interval)
        response = requests.post(base + 'token', data={
            'client_id': args.client_id, 'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
            'device_code': device['device_code']}, timeout=30)
        result = response.json()
        if response.ok:
            if not result.get('refresh_token'):
                raise SystemExit('No refresh token returned; offline access is required.')
            folder = Path(__file__).resolve().parent / 'data'
            folder.mkdir(exist_ok=True)
            path = folder / 'onedrive-secrets.json'
            with path.open('x', encoding='utf-8') as output:
                json.dump({'MS_CLIENT_ID': args.client_id, 'MS_REFRESH_TOKEN': result['refresh_token']}, output, indent=2)
            print('Sign-in complete. Copy the two values from sales automation/data/onedrive-secrets.json into the matching GitHub Secrets, then remove the local file.')
            return
        error = result.get('error')
        if error == 'authorization_pending':
            continue
        if error == 'slow_down':
            interval += 5
            continue
        raise SystemExit('Microsoft sign-in did not complete; no credential values were printed.')
    raise SystemExit('Sign-in expired; run the helper again.')


if __name__ == '__main__':
    main()
