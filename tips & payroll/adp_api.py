"""Read-only ADP Workforce Now worker roster (OAuth client credentials over mutual TLS).

Only GET requests to the workers resource. Nothing is created, changed or
imported in ADP. Credentials come from the environment; the certificate and key
are written to a private temporary folder for the duration of the read.
"""
import os
import re
import tempfile
from pathlib import Path

import requests

TOKEN_URL = 'https://accounts.adp.com/auth/oauth/v2/token'
WORKERS_URL = 'https://api.adp.com/hr/v2/workers'
COMPANIES = {'FND': 'CO', 'FNC': 'AZ', 'ZKT': 'TX'}
PAGE = 100


class ADPError(RuntimeError):
    pass


def missing_secrets():
    return [k for k in ('ADP_CLIENT_ID', 'ADP_CLIENT_SECRET', 'ADP_CERTIFICATE', 'ADP_PRIVATE_KEY') if not os.environ.get(k)]


def pem(value):
    """Accept PEM text or PEM with escaped newlines, as stored in a secret."""
    text = value.replace('\\n', '\n').strip()
    if '-----BEGIN' not in text:
        raise ADPError('ADP certificate or key is not PEM text')
    return text + '\n'


def text(value):
    if isinstance(value, dict):
        return str(value.get('codeValue') or value.get('shortName') or value.get('longName') or '').strip()
    return str(value or '').strip()


def worker_rows(worker):
    """One row per work assignment, with only payroll-identity fields."""
    person = worker.get('person', {})
    legal = person.get('legalName', {})
    status = text(worker.get('workerStatus', {}).get('statusCode'))
    rows = []
    for assignment in worker.get('workAssignments') or [{}]:
        location = assignment.get('homeWorkLocation', {})
        rows.append({
            'associate_oid': worker.get('associateOID', ''),
            'first_name': text(legal.get('givenName')),
            'last_name': text(legal.get('familyName1')),
            'preferred_name': text(person.get('preferredName', {}).get('givenName')),
            'worker_status': status,
            'assignment_status': text(assignment.get('assignmentStatus', {}).get('statusCode')),
            'primary': bool(assignment.get('primaryIndicator')),
            'company_code': text(assignment.get('payrollGroupCode')),
            'file_number': text(assignment.get('payrollFileNumber')),
            'job_title': text(assignment.get('jobTitle')) or text(assignment.get('jobCode')),
            'location': text(location.get('nameCode')) or text(location.get('address', {}).get('cityName')),
            'hire_date': text(assignment.get('hireDate') or worker.get('workerDates', {}).get('originalHireDate')),
            'termination_date': text(assignment.get('terminationDate') or worker.get('workerDates', {}).get('terminationDate')),
        })
    return rows


def fetch_workers(session=None):
    """Return every worker assignment visible to this ADP API client."""
    if missing_secrets():
        raise ADPError('Missing ADP secrets: ' + ', '.join(missing_secrets()))
    with tempfile.TemporaryDirectory() as folder:
        cert, key = Path(folder) / 'adp.pem', Path(folder) / 'adp.key'
        cert.write_text(pem(os.environ['ADP_CERTIFICATE']), encoding='utf-8')
        key.write_text(pem(os.environ['ADP_PRIVATE_KEY']), encoding='utf-8')
        os.chmod(key, 0o600)
        http = session or requests.Session()
        http.cert = (str(cert), str(key))
        try:
            token = http.post(TOKEN_URL, timeout=45, data={'grant_type': 'client_credentials'},
                              auth=(os.environ['ADP_CLIENT_ID'], os.environ['ADP_CLIENT_SECRET']))
        except requests.RequestException as exc:
            raise ADPError('ADP token request failed (' + type(exc).__name__ + ')') from None
        if token.status_code != 200 or not token.json().get('access_token'):
            detail = re.sub(r'[^A-Za-z0-9_ .:-]', '', str(token.json().get('error', '')) if token.headers.get('content-type', '').startswith('application/json') else '')[:60]
            raise ADPError(f'ADP token request returned HTTP {token.status_code} {detail}'.strip())
        headers = {'Authorization': 'Bearer ' + token.json()['access_token'], 'Accept': 'application/json'}
        rows, seen, skip = [], set(), 0
        while True:
            try:
                page = http.get(WORKERS_URL, headers=headers, timeout=90, params={'$top': PAGE, '$skip': skip})
            except requests.RequestException as exc:
                raise ADPError('ADP workers request failed (' + type(exc).__name__ + ')') from None
            if page.status_code == 204:
                break
            if page.status_code != 200:
                raise ADPError(f'ADP workers request returned HTTP {page.status_code}')
            workers = page.json().get('workers') or []
            if not workers:
                break
            new = [w for w in workers if w.get('associateOID') not in seen]
            if not new:
                raise ADPError('ADP repeated a page of workers; roster is incomplete')
            for worker in new:
                seen.add(worker.get('associateOID'))
                rows.extend(worker_rows(worker))
            skip += PAGE
            if skip > 20000:
                raise ADPError('ADP worker paging limit reached; roster is incomplete')
    return rows
