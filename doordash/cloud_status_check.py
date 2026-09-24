"""Read-only test of the authenticated status service used by the merchant portal."""
import json
import os
import re
import requests


def status_client():
    state = json.loads(os.environ['DOORDASH_STORAGE_STATE'])
    client = requests.Session()
    client.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36'
    client.headers['Origin'] = 'https://www.doordash.com'
    client.headers['Referer'] = 'https://www.doordash.com/'
    for cookie in state.get('cookies', []):
        if cookie.get('domain', '').lstrip('.') in ('doordash.com', 'www.doordash.com', 'merchant-portal.doordash.com'):
            client.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'], path=cookie.get('path', '/'))
    return client


def read_status(client, delivery):
    if not re.fullmatch(r'[a-fA-F0-9-]{36}', delivery):
        raise ValueError('Invalid historical delivery ID')
    response = client.get('https://merchant-portal.doordash.com/teammate-experience-service/api/v1/dispute-status/' + delivery, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f'Cloud status service returned HTTP {response.status_code}; no browser opened or data changed')
    value = response.json()
    if value.get('deliveryUuid') != delivery:
        raise RuntimeError('Status response identity mismatch')
    return value


def main():
    value = read_status(status_client(), os.environ['DOORDASH_CHECK_DELIVERY_ID'])
    print('Authenticated cloud status read succeeded:', value.get('disputeStatus'))


if __name__ == '__main__':
    main()
