"""Authenticated, encrypted transfer of an already reviewed payroll package.

Only encrypted bytes are stored as a private draft-release asset. Decrypted
payroll exists in the disposable runner directory, never repository history.
"""
import argparse
import base64
import hashlib
import io
import os
import re
import zipfile
from pathlib import Path

import requests
from nacl.secret import SecretBox


def unpack_package(ciphertext, key, expected_hash, destination):
    if not re.fullmatch(r'[a-f0-9]{64}', expected_hash):
        raise ValueError('Invalid approved package digest')
    if hashlib.sha256(ciphertext).hexdigest() != expected_hash:
        raise ValueError('Package differs from the approved upload')
    plaintext = SecretBox(base64.b64decode(key, validate=True)).decrypt(ciphertext)
    with zipfile.ZipFile(io.BytesIO(plaintext)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        sources = {'tcp-audit.json', 'native-payroll.xml', 'gateway-input.json'}
        if (len(entries) != 24 or len(set(names)) != 24 or not sources <= set(names)
                or sum(entry.file_size for entry in entries) > 50_000_000):
            raise ValueError('Unexpected payroll package contents')
        for name in names:
            if ('/' in name or '\\' in name or ':' in name or name.startswith('.')
                    or (name not in sources and not name.endswith('.xlsx'))):
                raise ValueError('Unsafe package filename')
        destination.mkdir(parents=True, exist_ok=False)
        for name in names:
            (destination / name).write_bytes(archive.read(name))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset-id', required=True, type=int)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    if (os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('GITHUB_REPOSITORY') != 'AsefiAb/LC-AUTOMATION'):
        raise ValueError('Private repository workflow required')
    url = 'https://api.github.com/repos/AsefiAb/LC-AUTOMATION'
    headers = {'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
               'Accept': 'application/vnd.github+json'}
    repo = requests.get(url, headers=headers, timeout=30)
    repo.raise_for_status()
    if repo.json().get('private') is not True:
        raise ValueError('Repository must be private')
    response = requests.get(url + '/releases/assets/' + str(args.asset_id),
                            headers={**headers, 'Accept': 'application/octet-stream'}, timeout=60)
    response.raise_for_status()
    if len(response.content) > 50_000_000:
        raise ValueError('Package too large')
    unpack_package(response.content, os.environ['PAYROLL_REVIEWED_PACKAGE_KEY'], args.sha256, args.folder)
    print('Approved encrypted package restored; source reconciliation follows.')


if __name__ == '__main__':
    main()
