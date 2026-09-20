"""Validate private, owner-authorized Altametrics session state without logging it."""
import json
from urllib.parse import urlsplit

REGIONS = {'CO': '3356', 'AZ': '3759', 'TX': '3910'}


def secret_name(region):
    if region not in REGIONS:
        raise ValueError('Unknown Altametrics region')
    return region + '_ALTAMETRICS_SESSION'


def alt_host(host):
    return bool(host and (host == 'altametrics.com' or host.endswith('.altametrics.com')))


def validate_session(value, region):
    try:
        doc = json.loads(value) if isinstance(value, str) else value
        if doc['version'] != 1 or doc['region'] != region:
            raise ValueError()
        url = urlsplit(doc['entry_url'])
        if url.scheme != 'https' or not alt_host(url.hostname) or url.username or url.password or 'ers_webApp' not in url.path:
            raise ValueError()
        state = doc['state']
        if not isinstance(state['cookies'], list) or not isinstance(state['origins'], list) or not state['cookies']:
            raise ValueError()
        for cookie in state['cookies']:
            if not alt_host(cookie['domain'].lstrip('.')) or not isinstance(cookie['value'], str) or not cookie['name']:
                raise ValueError()
        for origin in state['origins']:
            parsed = urlsplit(origin['origin'])
            if parsed.scheme != 'https' or not alt_host(parsed.hostname) or parsed.username or parsed.password:
                raise ValueError()
        if len(json.dumps(doc).encode()) > 40000:
            raise ValueError()
        return doc
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError('Invalid Altametrics session; reconnect the selected region') from None


def make_session(region, entry_url, state):
    # A dedicated sign-in context may also contain CAPTCHA-provider cookies.
    # Only Altametrics state belongs in the authorized reporting connection.
    filtered = {
        'cookies': [c for c in state['cookies'] if alt_host(c.get('domain', '').lstrip('.'))],
        'origins': [o for o in state['origins'] if alt_host(urlsplit(o.get('origin', '')).hostname)],
    }
    return validate_session({'version': 1, 'region': region, 'entry_url': entry_url, 'state': filtered}, region)
