"""Read-only ADP Workforce Now worker roster (OAuth client credentials over mutual TLS).

Only GET requests to the workers resource. Nothing is created, changed or
imported in ADP. Credentials come from the environment; the certificate and key
are written to a private temporary folder for the duration of the read.
"""
import io
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
            'hourly_rate': text((assignment.get('baseRemuneration') or {}).get('hourlyRateAmount', {}).get('amountValue')),
        })
    return rows


def _json_object(response, what):
    """The response body as a JSON object, or ADPError (never the raw body, which may echo URLs)."""
    try:
        data = response.json()
    except ValueError:  # Includes requests' JSONDecodeError, e.g. an HTML maintenance page.
        raise ADPError(f'ADP {what} returned HTTP {response.status_code} without JSON') from None
    if not isinstance(data, dict):
        raise ADPError(f'ADP {what} returned unexpected JSON')
    return data


def _token_error(token):
    """Short, sanitized OAuth error code for a failed token request."""
    if not str(token.headers.get('content-type', '')).startswith('application/json'):
        return ''
    try:
        body = token.json()
    except ValueError:
        return ''
    return re.sub(r'[^A-Za-z0-9_ .:-]', '', str(body.get('error', '')) if isinstance(body, dict) else '')[:60]


def fetch_workers(session=None):
    """Return every worker assignment visible to this ADP API client.

    Any unexpected response (no JSON, wrong JSON shape, malformed worker record)
    raises ADPError, so callers can fall back to the last supplied export.
    """
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
        if token.status_code != 200:
            raise ADPError(f'ADP token request returned HTTP {token.status_code} {_token_error(token)}'.strip())
        access = _json_object(token, 'token request').get('access_token')
        if not isinstance(access, str) or not access:
            raise ADPError('ADP token request returned HTTP 200 without an access token')
        headers = {'Authorization': 'Bearer ' + access, 'Accept': 'application/json'}
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
            workers = _json_object(page, 'workers request').get('workers') or []
            if not isinstance(workers, list) or not all(isinstance(w, dict) for w in workers):
                raise ADPError('ADP workers response was not a list of worker records')
            if not workers:
                break
            new = [w for w in workers if w.get('associateOID') not in seen]
            if not new:
                raise ADPError('ADP repeated a page of workers; roster is incomplete')
            for worker in new:
                seen.add(worker.get('associateOID'))
                try:
                    rows.extend(worker_rows(worker))
                except (AttributeError, TypeError, KeyError, ValueError):  # e.g. "legalName": null
                    raise ADPError('ADP returned a worker record in an unexpected shape; roster is incomplete') from None
            skip += PAGE
            if skip > 20000:
                raise ADPError('ADP worker paging limit reached; roster is incomplete')
    return rows


_CACHE = {}


def workers_once():
    """One ADP read per run; returns (rows, problem) and never raises.

    A failure of any kind is cached too, so ADP is not asked again in the same
    run. Only ADPError text (written by this module) is kept; any other error is
    reduced to its type name, because its message could carry request details
    and the problem text is copied into review sheets and result files.
    """
    if 'rows' not in _CACHE:
        try:
            rows, problem = fetch_workers(), None
        except ADPError as exc:
            rows, problem = None, str(exc)
        except Exception as exc:
            rows, problem = None, 'ADP roster read failed (' + type(exc).__name__ + ')'
        _CACHE['rows'], _CACHE['problem'] = rows, problem
    return _CACHE['rows'], _CACHE['problem']


def name_key(value):
    return ' '.join(str(value).casefold().split())


def is_active(worker):
    return (worker['worker_status'].lower() == 'active'
            and worker['assignment_status'].lower() in ('a', 'active', ''))


def sort_key(last, first):
    return (name_key(last), name_key(first))


def current_workers(workers, company=None):
    """One current assignment per ADP person and company.

    ADP returns one row per work assignment, and a person can hold several under
    the same associate ID: a terminated old assignment after a rehire, or an
    assignment without a file number. Only active assignments with a file number
    count. When one person still has more than one, the newest hire wins (owner:
    a rehire uses the most recent record); the primary assignment breaks ties on
    the same hire date, then the higher file number. Different people who share a
    name are never merged. Returns (current, older); each older assignment carries
    'replaced_by' (the current file number) and 'replaced_hire_date' (its hire date).
    """
    groups = {}
    for worker in workers:
        if (not is_active(worker) or not worker['file_number'].strip()
                or (company and worker['company_code'].upper() != company)):
            continue
        person = worker.get('associate_oid') or 'file:' + worker['file_number']
        groups.setdefault((worker['company_code'].upper(), person), []).append(worker)
    current, older = [], []
    for records in groups.values():
        records = sorted(records, key=lambda w: (w['hire_date'] or '', bool(w.get('primary')),
                                                 w['file_number'].lstrip('0').zfill(20)), reverse=True)
        current.append(records[0])
        older.extend({**w, 'replaced_by': records[0]['file_number'], 'replaced_hire_date': records[0]['hire_date']}
                     for w in records[1:])
    return current, older


def augment_template(raw, company, workers):
    """Insert active ADP workers missing from a native EPI template.

    Rows are placed by last name, then first name, using ADP's own name fields.
    Existing rows, control rows and amounts are kept byte-for-byte. New rows use
    the ADP file number (without leading zeros, like the existing export), the
    ADP legal name and hourly rate, and blank amounts for the payroll fill. When
    one person has several active ADP records, only the most recent one is added:
    the newest hire, with the primary assignment breaking ties (see
    current_workers). A name the template encoding cannot hold raises ADPError and
    nothing is inserted. Returns (new_raw, added_rows); raw itself is never changed.
    """
    import csv
    encoding = 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'
    try:
        content = raw.decode(encoding)
    except UnicodeDecodeError:
        encoding, content = 'cp1252', raw.decode('cp1252')
    newline = '\r\n' if '\r\n' in content else '\n'
    lines = content.split(newline)
    parsed = {i: next(csv.reader([line])) for i, line in enumerate(lines) if line.startswith(company + ',')}
    rows = [(i, cells) for i, cells in sorted(parsed.items()) if len(cells) == 9]
    if not rows:
        raise ADPError('EPI template has no employee rows for ' + company)
    batch = rows[0][1][1]
    current, _ = current_workers(workers, company)
    by_number = {w['file_number'].lstrip('0'): w for w in workers if w['company_code'].upper() == company}
    present = {cells[2].lstrip('0') for _, cells in rows}
    added = []
    for worker in current:
        number = worker['file_number'].lstrip('0')
        name = ' '.join(f"{worker['first_name']} {worker['last_name']}".replace(',', ' ').split())
        if not number or number in present or not name:
            continue
        try:
            rate = 'H ' + format(float(worker['hourly_rate']), '.4f') if worker['hourly_rate'] else ''
        except (ValueError, TypeError):
            rate = ''
        present.add(number)
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator='').writerow([company, batch, number, '', name, rate, '', '', ''])
        added.append({'file_number': number, 'name': name, 'rate': rate, 'hire_date': worker['hire_date'],
                      'key': sort_key(worker['last_name'], worker['first_name']), 'line': buffer.getvalue()})
    if not added:
        return raw, []

    def existing_key(cells):
        worker = by_number.get(cells[2].lstrip('0'))
        if worker:
            return sort_key(worker['last_name'], worker['first_name'])
        parts = cells[4].split()
        return sort_key(parts[-1] if parts else '', ' '.join(parts[:-1]))

    # Merge new rows into the existing order: each goes before the first existing
    # employee row that sorts after it, or after the last employee row.
    entries = [(existing_key(cells), lines[i]) for i, cells in rows]
    for row in sorted(added, key=lambda r: r['key']):
        position = next((n for n, (key, _) in enumerate(entries) if key > row['key']), len(entries))
        entries.insert(position, (row['key'], row['line']))
    first, last = rows[0][0], rows[-1][0]
    employee_indexes = {i for i, _ in rows}
    if any(lines[i].strip() for i in range(first, last + 1) if i not in employee_indexes):
        raise ADPError('EPI template employee rows are not contiguous; rows were not inserted')
    lines[first:last + 1] = [line for _, line in entries]
    try:
        new_raw = newline.join(lines).encode(encoding)
    except UnicodeEncodeError:
        raise ADPError(f'a new ADP legal name cannot be written in the template encoding ({encoding}); '
                       'rows were not inserted') from None
    for row in added:
        row.pop('key')
    return new_raw, added
