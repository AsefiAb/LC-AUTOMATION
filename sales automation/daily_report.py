"""Regional sales source -> existing OneDrive workbooks -> Gmail.

Altametrics supplies sales, transaction counts and Punched Labor %.
Use --preflight to inspect setup, --preview to prepare without writing/sending.
"""
import argparse
import base64
import hashlib
import json
import os
import smtplib
import ssl
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

import tcp_labor
import altametrics
from onedrive import OneDrive, DriveError
from workbooks import update, inspect_layout, WorkbookError

FOLDER = Path(__file__).resolve().parent


class ReportError(RuntimeError):
    pass


def report_date(now, timezone_name):
    return (now.astimezone(ZoneInfo(timezone_name)).date() - timedelta(days=1)).isoformat()


def checked_date(value, config):
    target = date.fromisoformat(value)
    today = datetime.now(ZoneInfo(config['timezone'])).date()
    if target >= today:
        raise ReportError('Daily report must cover a completed date before today.')
    return value


def setup_issues(config, require_email=True):
    issues = []
    source = config.get('source')
    keys = ['MS_CLIENT_ID', 'MS_REFRESH_TOKEN']
    if source == 'Altametrics':
        keys += altametrics.missing_secrets()
    elif source == 'TCP':
        if not config.get('tcp_metrics'):
            issues.append('TCP has no confirmed sales/transaction/actual labor-cost endpoint configured.')
        if not config.get('labor_cost_verified'):
            issues.append('TCP actual labor-cost definition has not been verified.')
        keys += ['TCP_CLIENT_ID', 'TCP_CLIENT_SECRET', 'TCP_API_KEY']
    else:
        issues.append('Unknown sales source; use the explicitly configured Altametrics or TCP adapter.')
    if require_email:
        keys.append('CAAZ_GMAIL_APP_PASS')
    for key in keys:
        if not os.environ.get(key):
            issues.append('Missing secret: ' + key)
    return issues


def field(record, path):
    value = record
    for part in path.split('.'):
        if not isinstance(value, dict) or part not in value:
            raise ReportError('TCP metric response is missing mapped field ' + path)
        value = value[part]
    return value


def normalize_store(value):
    import re
    match = re.fullmatch(r'0*(\d+)-0*(\d+)', str(value))
    if not match:
        raise ReportError('TCP metric response has an invalid store identifier.')
    return f'{int(match[1])}-{int(match[2]):02d}'


def fetch_metrics(day, config):
    mapping = config.get('tcp_metrics')
    if not mapping or mapping.get('verified') is not True:
        raise ReportError('TCP sales/transactions endpoint is not confirmed. No alternative source will be used.')
    resource = mapping.get('resource', '')
    import re
    if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9/-]*', resource) or '..' in resource:
        raise ReportError('Invalid TCP metric resource; only relative TCP v1 resources are supported.')
    if set(mapping.get('fields', {})) != {'store', 'date', 'sales', 'transactions', 'labor_cost'}:
        raise ReportError('TCP metric field mapping is incomplete.')
    params = {key: day if value == '{report_date}' else value
              for key, value in mapping.get('params', {}).items()}
    client = tcp_labor.TCP()
    client.authenticate()
    records = tcp_labor.collect(client, resource, params)
    rows = []
    for record in records:
        mapped = {name: field(record, path) for name, path in mapping['fields'].items()}
        if str(mapped['date']) != day:
            raise ReportError('TCP metrics contain a date outside the requested report day.')
        mapped['store'] = normalize_store(mapped['store'])
        rows.append(mapped)
    return rows


def validate(day, metric_rows, labor, config):
    if not labor.get('complete') or labor.get('issues') or labor.get('report_date') != day:
        raise ReportError('TCP hours are incomplete or dated incorrectly; no workbooks or email were changed.')
    if not config.get('labor_cost_verified'):
        raise ReportError('Unverified TCP labor cost must not be published as actual labor percentage.')
    expected = {s['store']: s for s in config['stores']}
    metrics = {r['store']: r for r in metric_rows}
    hours = {r['store']: r for r in labor['stores']}
    if len(metrics) != len(metric_rows) or set(metrics) != set(expected):
        raise ReportError('TCP sales data has duplicate, missing, or unexpected stores.')
    if len(hours) != len(labor['stores']) or set(hours) != set(expected):
        raise ReportError('TCP hours have duplicate, missing, or unexpected stores.')
    output = []
    for store, definition in expected.items():
        m, h = metrics[store], hours[store]
        if m['date'] != day:
            raise ReportError('TCP metric dates do not match.')
        sales = tcp_labor.decimal(m['sales'])
        trans = tcp_labor.decimal(m['transactions'])
        cost = tcp_labor.decimal(m['labor_cost'])
        if trans != trans.to_integral_value():
            raise ReportError('TCP transaction counts must be whole numbers.')
        total = tcp_labor.decimal(h['total_hours'])
        regular, ot = tcp_labor.decimal(h['regular_hours']), tcp_labor.decimal(h['ot_hours'])
        if abs(total - regular - ot) > Decimal('0.01'):
            raise ReportError('TCP decimal hour totals do not reconcile.')
        output.append({**definition, 'report_date': day,
            'sales': tcp_labor.money(sales), 'transactions': int(trans),
            'labor_cost': tcp_labor.money(cost),
            'labor_pct': tcp_labor.money(cost / sales * 100) if sales else None,
            'regular_hours': str(regular), 'ot_hours': str(ot), 'total_hours': str(total)})
    return output


def email_message(day, rows, attachments, config):
    msg = EmailMessage()
    msg['From'] = config['sender']
    msg['To'] = ', '.join(config['to'])
    msg['Cc'] = ', '.join(config['cc'])
    msg['Subject'] = 'Daily Sales & Labor ' + day
    msg['Message-ID'] = '<sales-' + day + '@caazllc.com>'
    lines = []
    for region in config['workbooks']:
        lines.append(region['name'])
        for row in rows:
            if row['region'] != region['region']:
                continue
            pct = row['labor_pct'] + '%' if row['labor_pct'] is not None else 'N/A (zero sales)'
            lines.append(f"{row['store']}: Sales ${row['sales']} | Transactions {row['transactions']} | Labor {pct} | Goal {row['labor_goal_pct']}%")
        lines.append('')
    lines.extend(['Thanks,', 'AB'])
    msg.set_content('\n'.join(lines))
    for path in attachments:
        msg.add_attachment(path.read_bytes(), maintype='application',
            subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename=path.name)
    return msg


class DeliveryLedger:
    """Small non-financial delivery receipt in the private repo; reserve before SMTP.

    Unknown send outcomes stay reserved and require checking Gmail Sent manually.
    A dedicated branch keeps receipts out of the application checkout.
    """
    def __init__(self, day, namespace='sales'):
        if namespace not in ('sales', 'payroll'):
            raise ReportError('Unknown delivery namespace')
        repo = os.environ.get('GITHUB_REPOSITORY', '')
        if repo != 'AsefiAb/LC-AUTOMATION' or not os.environ.get('GITHUB_TOKEN'):
            raise ReportError('Live delivery requires the private repository workflow and its delivery ledger.')
        self.url = 'https://api.github.com/repos/' + repo
        self.headers = {'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
                        'Accept': 'application/vnd.github+json'}
        self.branch = namespace + '-delivery-state'
        self.path = '/contents/deliveries/' + day + '.json'
        self.sha = None
        current = self.call('GET', '/git/ref/heads/' + self.branch, allowed=(200, 404))
        if current.status_code == 404:
            head = self.call('GET', '/git/ref/heads/main').json()['object']['sha']
            self.call('POST', '/git/refs', json={'ref': 'refs/heads/' + self.branch, 'sha': head})

    def call(self, method, path, allowed=(200, 201), **kwargs):
        response = requests.request(method, self.url + path, headers=self.headers, timeout=45, **kwargs)
        if response.status_code not in allowed:
            raise ReportError('Delivery ledger request failed (HTTP ' + str(response.status_code) + ').')
        return response

    def read(self):
        response = self.call('GET', self.path, params={'ref': self.branch}, allowed=(200, 404))
        if response.status_code == 404:
            return None
        data = response.json()
        self.sha = data['sha']
        return json.loads(base64.b64decode(data['content']))

    def write(self, value):
        payload = {'message': 'Record daily sales delivery state', 'branch': self.branch,
                   'content': base64.b64encode(json.dumps(value).encode()).decode()}
        if self.sha:
            payload['sha'] = self.sha
        result = self.call('PUT', self.path, json=payload).json()
        self.sha = result['content']['sha']


def deliver(message, ledger, day):
    digest = hashlib.sha256(message.as_bytes()).hexdigest()
    previous = ledger.read()
    if previous:
        if previous.get('status') == 'sent':
            return 'already sent'
        raise ReportError('A prior send has an uncertain outcome. Check Gmail Sent before manually clearing its ledger receipt.')
    password = os.environ.get('CAAZ_GMAIL_APP_PASS', '').replace(' ', '')
    if not password:
        raise ReportError('Missing CAAZ_GMAIL_APP_PASS.')
    receipt = {'date': day, 'status': 'sending', 'message_sha256': digest,
               'message_id': message['Message-ID'], 'run_id': os.environ.get('GITHUB_RUN_ID')}
    ledger.write(receipt)
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ssl.create_default_context(), timeout=60) as smtp:
            smtp.login(message['From'], password)
            refused = smtp.send_message(message)
            if refused:
                raise ReportError('Some recipients were rejected; delivery is partial and requires review.')
    except (smtplib.SMTPException, OSError):
        raise ReportError('Email was not confirmed delivered. The ledger prevents automatic duplicate sends.') from None
    ledger.write({**receipt, 'status': 'sent', 'confirmed_at': datetime.now(timezone.utc).isoformat()})
    return 'sent'


def check_email_login():
    password = os.environ.get('CAAZ_GMAIL_APP_PASS', '').replace(' ', '')
    if not password:
        raise ReportError('Missing CAAZ_GMAIL_APP_PASS')
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login('ab@caazllc.com', password)
    except (smtplib.SMTPException, OSError):
        raise ReportError('Gmail authentication failed; no email sent') from None
    print('Gmail authentication confirmed; no email sent', flush=True)


def run(day, config, preview=False):
    issues = setup_issues(config, require_email=not preview)
    if issues:
        raise ReportError('\n'.join(issues))
    # Check receipt before any workbook writes on a repeated run.
    ledger = None if preview else DeliveryLedger(day)
    if ledger:
        previous = ledger.read()
        if previous and previous.get('status') == 'sent':
            print('This report date was already delivered; no changes made.')
            return
        if previous:
            raise ReportError('A previous delivery is unresolved; inspect its receipt before retrying.')
    if config.get('source') == 'Altametrics':
        rows = altametrics.validate(day, altametrics.fetch(day, config), config)
    else:
        labor = tcp_labor.fetch(day, config)
        rows = validate(day, fetch_metrics(day, config), labor, config)
    drive = OneDrive()
    prepared = []
    output = FOLDER / 'reports' / day
    output.mkdir(parents=True, exist_ok=True)
    for book in config['workbooks']:
        snapshot = drive.read(book['url'])
        region_rows = [row for row in rows if row['region'] == book['region']]
        content, changes = update(snapshot['content'], region_rows, day)
        path = output / (book['region'] + ' Sales ' + day + '.xlsx')
        prepared.append((book, snapshot, content, changes, path))
    # All three files are validated before the first upload.
    for book, snapshot, content, changes, path in prepared:
        if not preview and changes:
            drive.write(snapshot, content)
            verified = drive.read(book['url'])
            _, remaining = update(verified['content'], [r for r in rows if r['region'] == book['region']], day)
            if remaining:
                raise ReportError('OneDrive did not retain all expected updates.')
            content = verified['content']
        path.write_bytes(content)
    message = email_message(day, rows, [item[4] for item in prepared], config)
    if len(message.as_bytes()) > 24_000_000:
        raise ReportError('Email attachments exceed the supported 24 MB encoded-message limit.')
    (output / 'email-preview.eml').write_bytes(message.as_bytes())
    (output / 'store-totals.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    if preview:
        print('Private preview prepared. No workbook uploads or email sends performed.')
    else:
        print('Daily report ' + deliver(message, ledger, day) + '.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date')
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--schedule-gate', action='store_true')
    args = parser.parse_args()
    config = json.loads((FOLDER / 'config.json').read_text())
    now = datetime.now(timezone.utc)
    if args.schedule_gate:
        local = now.astimezone(ZoneInfo(config['timezone']))
        eligible = local.hour >= config['run_hour']
        print('true' if eligible else 'false')
        return 0
    day = checked_date(args.date or report_date(now, config['timezone']), config)
    if args.preflight:
        issues = setup_issues(config)
        if os.environ.get('CAAZ_GMAIL_APP_PASS'):
            try:
                check_email_login()
            except ReportError as exc:
                issues.append(str(exc))
        # Test OneDrive independently: a blocked sales portal must not hide
        # whether the Microsoft connection and workbook links are usable.
        if all(os.environ.get(k) for k in ('MS_CLIENT_ID', 'MS_REFRESH_TOKEN')):
            try:
                drive = OneDrive()
                for book in config['workbooks']:
                    snapshot = drive.read(book['url'])
                    private_copy = FOLDER / 'reports' / 'onedrive-check' / (book['region'] + '.xlsx')
                    private_copy.parent.mkdir(parents=True, exist_ok=True)
                    private_copy.write_bytes(snapshot['content'])
                    print(book['region'] + ': existing OneDrive workbook read successfully; no changes made', flush=True)
                    try:
                        stores = [s['store'] for s in config['stores'] if s['region'] == book['region']]
                        inspect_layout(snapshot['content'], stores, day)
                    except WorkbookError as exc:
                        issues.append(book['region'] + ': ' + str(exc))
            except (DriveError, WorkbookError) as exc:
                issues.append(str(exc))
            except Exception as exc:
                issues.append('OneDrive check failed (' + type(exc).__name__ + ')')
        if config.get('source') == 'Altametrics' and not altametrics.missing_secrets():
            try:
                rows = altametrics.validate(day, altametrics.fetch(day, config), config)
                output = FOLDER / 'reports' / ('altametrics-' + day + '.json')
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(rows, indent=2), encoding='utf-8')
            except (altametrics.AltametricsError, ValueError) as exc:
                issues.append(str(exc))
        if config.get('source') == 'TCP' and all(os.environ.get(k) for k in ('TCP_CLIENT_ID', 'TCP_CLIENT_SECRET', 'TCP_API_KEY')):
            labor = tcp_labor.fetch(day, config)
            output = FOLDER / 'reports' / ('tcp-labor-' + day + '.json')
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(labor, indent=2), encoding='utf-8')
            issues.extend(labor['issues'])
        print(json.dumps({'report_date': day, 'ready': not issues, 'issues': issues}, indent=2))
        return 1 if issues else 0
    run(day, config, args.preview)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ReportError, DriveError, WorkbookError, tcp_labor.InspectionError, altametrics.AltametricsError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # Do not let transport tracebacks print tokens, signed links, or records.
        print('Unexpected failure (' + type(exc).__name__ + '); no success is assumed.', file=sys.stderr)
        sys.exit(1)
