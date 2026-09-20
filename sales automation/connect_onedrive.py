"""One-time local Microsoft sign-in. Never run this helper in GitHub Actions."""
import argparse
import json
import os
import time
import subprocess
import base64
from pathlib import Path
import requests


class GitHubSecrets:
    """Encrypt locally and upload only to the user's private automation repo."""
    def __init__(self, allowed_names=None):
        self.allowed_names = set(allowed_names or ('MS_CLIENT_ID', 'MS_REFRESH_TOKEN'))
        permitted = {'MS_CLIENT_ID', 'MS_REFRESH_TOKEN', 'CO_ALTAMETRICS_SESSION', 'AZ_ALTAMETRICS_SESSION', 'TX_ALTAMETRICS_SESSION'}
        if not self.allowed_names <= permitted:
            raise RuntimeError('Unsupported connection secret')
        from nacl.public import PublicKey, SealedBox
        from nacl.encoding import Base64Encoder
        credential = subprocess.run(['git', 'credential', 'fill'],
            input='protocol=https\nhost=github.com\n\n', text=True, capture_output=True, timeout=30,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
        values = dict(line.split('=', 1) for line in credential.stdout.splitlines() if '=' in line)
        if credential.returncode or not values.get('password'):
            raise RuntimeError('GitHub authentication is unavailable')
        self.headers = {'Authorization': 'Bearer ' + values['password'], 'Accept': 'application/vnd.github+json'}
        self.url = 'https://api.github.com/repos/AsefiAb/LC-AUTOMATION'
        repository = requests.get(self.url, headers=self.headers, timeout=30)
        if not repository.ok or repository.json().get('private') is not True:
            raise RuntimeError('The configured private GitHub repository is unavailable')
        response = requests.get(self.url + '/actions/secrets/public-key', headers=self.headers, timeout=30)
        if not response.ok:
            raise RuntimeError('GitHub secret encryption key is unavailable')
        key = response.json()
        self.key_id = key['key_id']
        self.box = SealedBox(PublicKey(key['key'], Base64Encoder))

    def save(self, name, value):
        if name not in self.allowed_names:
            raise RuntimeError('This helper cannot save that connection secret')
        encrypted = base64.b64encode(self.box.encrypt(value.encode())).decode()
        response = requests.put(self.url + '/actions/secrets/' + name, headers=self.headers,
            json={'key_id': self.key_id, 'encrypted_value': encrypted}, timeout=30)
        if response.status_code not in (201, 204):
            raise RuntimeError('GitHub did not confirm saving ' + name)


def main():
    if os.environ.get('GITHUB_ACTIONS'):
        raise SystemExit('Run the OneDrive sign-in helper locally, not in Actions.')
    parser = argparse.ArgumentParser()
    parser.add_argument('--client-id', required=True, help='Your Microsoft public-client application ID')
    parser.add_argument('--github', action='store_true', help='Encrypt and save directly to LC-AUTOMATION secrets instead of a local file')
    args = parser.parse_args()
    destination = GitHubSecrets() if args.github else None
    base = 'https://login.microsoftonline.com/common/oauth2/v2.0/'
    response = requests.post(base + 'devicecode', data={
        'client_id': args.client_id,
        'scope': 'https://graph.microsoft.com/Files.ReadWrite offline_access'}, timeout=30)
    if not response.ok:
        raise SystemExit('Microsoft rejected device sign-in. Check the app ID and public-client setup.')
    device = response.json()
    print(device['message'], flush=True)
    deadline = time.monotonic() + device['expires_in']
    interval = max(5, device.get('interval', 5))
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            response = requests.post(base + 'token', data={
                'client_id': args.client_id, 'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
                'device_code': device['device_code']}, timeout=30)
        except requests.RequestException:
            print('Temporary connection interruption; still waiting for Microsoft sign-in.', flush=True)
            continue
        result = response.json()
        if response.ok:
            if not result.get('refresh_token'):
                raise SystemExit('No refresh token returned; offline access is required.')
            if destination:
                print('Microsoft sign-in confirmed. Saving encrypted GitHub secrets.', flush=True)
                destination.save('MS_CLIENT_ID', args.client_id)
                destination.save('MS_REFRESH_TOKEN', result['refresh_token'])
                print('Microsoft connection saved to the two LC-AUTOMATION GitHub secrets; no token file was written.', flush=True)
                return
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
    try:
        main()
    except Exception as exc:
        raise SystemExit('Connection setup stopped (' + type(exc).__name__ + '); sensitive details withheld.') from None
