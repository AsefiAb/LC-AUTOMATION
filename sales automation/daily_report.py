"""Regional sales source -> existing OneDrive workbooks -> Gmail.

Altametrics supplies sales and transactions; TCP supplies a labeled wage estimate.
Use --preflight to inspect setup, --preview to prepare without writing/sending.
"""
import argparse
import base64
import hashlib
import json
import os
import re
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


def update_run_status(reset=False, **values):
    """Private structured progress for truthful owner-only failure notices."""
    path = FOLDER / 'reports' / 'run-status.json'
    status = {}
    if path.exists() and not reset:
        status = json.loads(path.read_text(encoding='utf-8'))
    status.update(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2), encoding='utf-8')


def safe_failure(exc):
    """Translate errors without placing source URLs or exception payloads in email."""
    detail = str(exc).lower()
    regions = sorted(set(re.findall(r'(?:^|;\s*)(CO|AZ|TX):', str(exc))))
    if 'password change' in detail:
        cause = 'Altametrics requires a password change for a regional account.'
    elif 'interactive' in detail or 'verification' in detail or 'security check' in detail:
        cause = 'Altametrics requested an interactive security check or account sign-in.'
    elif isinstance(exc, DriveError):
        cause = 'OneDrive access, download, upload or file-version verification failed.'
    elif isinstance(exc, WorkbookError):
        cause = 'Workbook layout, existing figures or uploaded values did not pass validation.'
    elif 'email' in detail or 'smtp' in detail or 'gmail' in detail or 'delivery' in detail:
        cause = 'Email delivery or its duplicate-prevention receipt was not confirmed.'
    elif isinstance(exc, altametrics.AltametricsError):
        cause = 'Altametrics source collection did not complete.'
    elif isinstance(exc, ValueError):
        cause = 'Report inputs or source figures did not pass validation.'
    else:
        cause = 'The reporting process stopped before completion.'
    return {'regions': regions, 'cause': cause}


def record_failure(exc):
    try:
        update_run_status(complete=False, failure=safe_failure(exc))
    except Exception:
        pass  # Never hide the original failed exit when diagnostics cannot save.


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
        if config.get('labor_source') in ('TCP base wage estimate', 'TCP wage estimate'):
            keys += ['TCP_CLIENT_ID', 'TCP_CLIENT_SECRET', 'TCP_API_KEY']
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


def apply_tcp_estimate(day, rows, labor):
    """Never replace missing TCP data with zero or publish partial store costs."""
    expected = {row['store'] for row in rows}
    hours = {row['store']: row for row in labor.get('stores', [])}
    problem = None
    if labor.get('report_date') != day or any(row['report_date'] != day for row in rows):
        problem = 'TCP dates do not match the sales date'
    elif len(hours) != len(labor.get('stores', [])) or set(hours) != expected:
        problem = 'TCP store coverage did not reconcile'
    output = []
    for row in rows:
        h = hours.get(row['store'], {})
        reasons = [problem] if problem else list(h.get('issues', []))
        if not reasons and not h.get('complete'):
            reasons = ['TCP hours or rates are incomplete']
        cost = None
        if not reasons:
            try:
                cost = tcp_labor.decimal(h.get('wage_estimate') if labor.get('cost_model') == 'altametrics_wage_formula' else h.get('base_wage_estimate'))
                if not h.get('segments') or h.get('rate_segments') != h['segments']:
                    raise ValueError('Incomplete wage rates')
                if cost == 0 and tcp_labor.decimal(h.get('total_hours')) > 0:
                    raise ValueError('Zero cost with worked hours')
            except ValueError:
                reasons = ['TCP wage rates are incomplete']
                cost = None
        sales = tcp_labor.decimal(row['sales'])
        output.append({**row, 'labor_source': 'TCP wage estimate' if labor.get('cost_model') == 'altametrics_wage_formula' else 'TCP base wage estimate',
            'labor_cutoff_hour_local': labor.get('cutoff_hour_local', 0),
            'cost_model': labor.get('cost_model', 'base_wages'),
            'labor_pct': tcp_labor.money(cost / sales * 100) if cost is not None and sales else None,
            'labor_status': 'unavailable' if reasons else ('zero_sales' if not sales else 'estimate'),
            'labor_issues': reasons, 'base_wage_estimate': h.get('base_wage_estimate') if not reasons else None,
            'wage_estimate': tcp_labor.money(cost) if cost is not None else None,
            'total_hours': h.get('total_hours') if not reasons else None})
    return output


NO_REPORT_NOTE = ('Stores marked "No Altametrics report" had no Daily Activity Report for this date. '
                  'Their workbook cells were left blank and are filled automatically when the report '
                  'appears within seven days.')


def email_message(day, rows, attachments, config, unavailable=()):
    msg = EmailMessage()
    msg['From'] = config['sender']
    msg['To'] = ', '.join(config['to'])
    msg['Cc'] = ', '.join(config['cc'])
    msg['Subject'] = 'Daily Sales & Labor ' + day
    msg['Message-ID'] = '<sales-' + day + '@caazllc.com>'
    lines = ['Sales and transactions are in the attached Excel workbooks.', '']
    missing = {item['store'] for item in unavailable}
    by_store = {row['store']: row for row in rows}
    for region in config['workbooks']:
        lines.append(region['name'])
        for store in (s for s in config['stores'] if s['region'] == region['region']):
            row = by_store.get(store['store'])
            if row is None:
                if store['store'] in missing:
                    lines.append(f"{store['store']}: No Altametrics report for this date | Goal {store['labor_goal_pct']}%")
                continue
            if row['labor_pct'] is not None:
                pct = row['labor_pct'] + '%'
                if row.get('labor_source') == 'TCP base wage estimate':
                    pct += ' (TCP estimate)'
            elif Decimal(row['sales']) == 0:
                pct = 'N/A (zero sales)'
            else:
                reasons = row.get('labor_issues') or ['Altametrics shows 0%; needs verification']
                pct = 'unavailable (' + '; '.join(reasons) + ')'
            lines.append(f"{row['store']}: Labor {pct} | Goal {row['labor_goal_pct']}%")
        lines.append('')
    if any(row.get('labor_source') == 'TCP wage estimate' for row in rows):
        cutoffs = sorted({row['labor_cutoff_hour_local'] for row in rows if row.get('labor_source') == 'TCP wage estimate'})
        boundary = ', '.join(f'{hour:02d}:00' for hour in cutoffs)
        lines.extend(['Labor is calculated from TCP recorded regular and overtime wages divided by Altametrics net sales. '
            'Includes crew and management. Business day starts at ' + boundary + ' store local time; whole shifts follow their start time. '
            'Excludes taxes and benefits; figures can change when timecards or rates are corrected.', ''])
    elif any(row.get('labor_source') == 'TCP base wage estimate' for row in rows):
        lines.extend(['TCP estimate = calculated worked hours × returned wage rate ÷ Altametrics net sales. '
            'Excludes overtime premiums, salary allocation, taxes and benefits. '
            'Overnight hours use the clock-in date; alignment with the sales business day is not yet verified.', ''])
    if missing:
        lines.extend([NO_REPORT_NOTE, ''])
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
        if namespace not in ('sales', 'payroll', 'sales-alert'):
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


def record_catch_up(day, status):
    """Note the catch-up outcome on a sent receipt so later slots can skip at the gate.

    The marker only saves work; it never changes whether an email is sent.
    """
    try:
        ledger = DeliveryLedger(day)
        receipt = ledger.read()
        if receipt and receipt.get('status') == 'sent' and receipt.get('catch_up') != status:
            ledger.write({**receipt, 'catch_up': status})
    except Exception:
        pass


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


def reviewed_day(path, sha256, day, config):
    """Rows for one report date from an owner-reviewed capture (manual runs only).

    Used when Altametrics blocks the automated login: the owner signs in and the
    captured Product Net and transactions are checked exactly as for workbook
    repairs (digest, schema, every store and date). The capture must cover all
    three regions so the email is never partial.
    """
    from repair_workbooks import reviewed_source
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    selected, by_day, payload = reviewed_source(path, sha256, payload.get('start'), payload.get('end'), config)
    if {b['region'] for b in selected['workbooks']} != {b['region'] for b in config['workbooks']}:
        raise ReportError('A reviewed source for the daily email must cover every region.')
    if day not in by_day:
        raise ReportError('The reviewed source does not cover this report date.')
    unavailable = [dict(item) for item in payload.get('unavailable', []) if item['date'] == day]
    rows = [{**row, 'labor_pct': None, 'total_hours': None, 'source_labor_pct': None,
             'labor_status': 'unverified_zero' if Decimal(row['sales']) else 'zero_sales',
             'labor_source': 'Owner-reviewed Altametrics capture'} for row in by_day[day]]
    return rows, unavailable


def run(day, config, preview=False, reviewed=None):
    update_run_status(phase='current report', report_date=day)
    issues = setup_issues(config, require_email=not preview)
    if issues:
        raise ReportError('\n'.join(issues))
    # Check receipt before any workbook writes on a repeated run.
    ledger = None if preview else DeliveryLedger(day)
    if ledger:
        previous = ledger.read()
        if previous and previous.get('status') == 'sent':
            update_run_status(current_delivery='already_sent')
            print('This report date was already delivered; no changes made.')
            return
        if previous:
            raise ReportError('A previous delivery is unresolved; inspect its receipt before retrying.')
    unavailable = []
    if config.get('source') == 'Altametrics':
        update_run_status(phase='source reports')
        # A store whose dashboard states it has no report for the date is listed
        # in the email and keeps blank workbook cells; it never blocks the others.
        if reviewed:
            rows, unavailable = reviewed_day(reviewed[0], reviewed[1], day, config)
            print('Using the owner-reviewed Altametrics capture for ' + day + '.', flush=True)
        else:
            rows, unavailable = altametrics.validate_partial(day, altametrics.fetch(day, config, allow_missing=True), config)
        if unavailable:
            update_run_status(unavailable_stores=[item['store'] for item in unavailable])
            print('No Altametrics report for ' + ', '.join(item['store'] for item in unavailable)
                  + ' on ' + day + '; those workbook cells stay blank.', flush=True)
        if config.get('labor_source') in ('TCP base wage estimate', 'TCP wage estimate'):
            try:
                labor = tcp_labor.fetch(day, config)
            except (tcp_labor.InspectionError, ValueError):
                # Do not leak API responses or replace a failed request with old data.
                labor = {'report_date': day, 'stores': [], 'complete': False,
                         'issues': ['TCP labor request failed; no estimate available.']}
            available = {row['store'] for row in rows}
            rows = apply_tcp_estimate(day, rows, {**labor, 'stores': [s for s in labor.get('stores', []) if s['store'] in available]})
    else:
        labor = tcp_labor.fetch(day, config)
        rows = validate(day, fetch_metrics(day, config), labor, config)
    update_run_status(phase='workbook preparation',
                      source_regions_verified=[book['region'] for book in config['workbooks']])
    drive = OneDrive()
    prepared = []
    output = FOLDER / 'reports' / day
    output.mkdir(parents=True, exist_ok=True)
    if config.get('labor_source') in ('TCP base wage estimate', 'TCP wage estimate') or config.get('source') == 'TCP':
        (output / 'tcp-labor.json').write_text(json.dumps(labor, indent=2), encoding='utf-8')
    # Preserve source totals before a workbook conflict or download failure.
    (output / 'store-totals.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    if unavailable:
        (output / 'unavailable-stores.json').write_text(json.dumps(unavailable, indent=2), encoding='utf-8')
    whole_dollars = config.get('workbook_sales_rounding') == 'whole_dollars'
    for book in config['workbooks']:
        update_run_status(active_region=book['region'])
        snapshot = drive.read(book['url'])
        region_rows = [row for row in rows if row['region'] == book['region']]
        layout = [s['store'] for s in config['stores'] if s['region'] == book['region']]
        if region_rows:
            content, changes = update(snapshot['content'], region_rows, day,
                sales_whole_dollars=whole_dollars, store_layout=layout)
        else:
            content, changes = snapshot['content'], []  # Every store unavailable: attach unchanged.
        path = output / (book['region'] + ' Sales ' + day + '.xlsx')
        prepared.append((book, snapshot, content, changes, path))
    # All three files are validated before the first upload.
    verified_regions = []
    for book, snapshot, content, changes, path in prepared:
        update_run_status(phase='workbook update', active_region=book['region'])
        if not preview and changes:
            update_run_status(workbook_updates_started=True)
            drive.write(snapshot, content)
            verified = drive.read(book['url'])
            _, remaining = update(verified['content'], [r for r in rows if r['region'] == book['region']], day,
                sales_whole_dollars=whole_dollars,
                store_layout=[s['store'] for s in config['stores'] if s['region'] == book['region']])
            if remaining:
                raise ReportError('OneDrive did not retain all expected updates.')
            content = verified['content']
        path.write_bytes(content)
        if not preview:
            verified_regions.append(book['region'])
            update_run_status(workbook_regions_verified=verified_regions)
    message = email_message(day, rows, [item[4] for item in prepared], config, unavailable)
    if len(message.as_bytes()) > 24_000_000:
        raise ReportError('Email attachments exceed the supported 24 MB encoded-message limit.')
    (output / 'email-preview.eml').write_bytes(message.as_bytes())
    (output / 'store-totals.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    if preview:
        print('Private preview prepared. No workbook uploads or email sends performed.')
    else:
        update_run_status(phase='email delivery', active_region=None, current_delivery='unconfirmed')
        delivered = deliver(message, ledger, day)
        update_run_status(current_delivery='sent' if delivered == 'sent' else 'already_sent')
        print('Daily report ' + delivered + '.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date')
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--schedule-gate', action='store_true')
    parser.add_argument('--catch-up-days', type=int, default=0)
    parser.add_argument('--source-records', help='Owner-reviewed capture JSON (manual runs only)')
    parser.add_argument('--source-sha256', help='Exact SHA-256 of the reviewed capture file')
    args = parser.parse_args()
    config = json.loads((FOLDER / 'config.json').read_text())
    now = datetime.now(timezone.utc)
    if args.schedule_gate:
        from schedule_gate import eligible
        print('true' if eligible(now, config) else 'false')
        return 0
    day = checked_date(args.date or report_date(now, config['timezone']), config)
    update_run_status(reset=True, report_date=day, complete=False,
        current_delivery='not_attempted', workbook_updates_started=False,
        workbook_regions_verified=[], source_regions_verified=[],
        recovery={'status': 'not_requested'})
    if not 0 <= args.catch_up_days <= 14:
        raise ReportError('Catch-up window must be between 0 and 14 days.')
    if args.catch_up_days and (args.preview or args.preflight):
        raise ReportError('Preview/preflight cannot perform live workbook catch-up.')
    reviewed = None
    if args.source_records or args.source_sha256:
        if not (args.source_records and args.source_sha256) or args.preflight or args.catch_up_days:
            raise ReportError('A reviewed source needs both its file and digest, and only for a manual preview or delivery.')
        reviewed = (args.source_records, args.source_sha256)
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
                rows, unavailable = altametrics.validate_partial(day, altametrics.fetch(day, config, allow_missing=True), config)
                output = FOLDER / 'reports' / ('altametrics-' + day + '.json')
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(rows, indent=2), encoding='utf-8')
                issues.extend(item['store'] + ': no Altametrics report for ' + day + ' (email would say so; cells stay blank)'
                              for item in unavailable)
            except (altametrics.AltametricsError, ValueError) as exc:
                issues.append(str(exc))
        if (config.get('source') == 'TCP' or config.get('labor_source') in ('TCP base wage estimate', 'TCP wage estimate')) and all(
                os.environ.get(k) for k in ('TCP_CLIENT_ID', 'TCP_CLIENT_SECRET', 'TCP_API_KEY')):
            try:
                labor = tcp_labor.fetch(day, config)
                output = FOLDER / 'reports' / ('tcp-labor-' + day + '.json')
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(labor, indent=2), encoding='utf-8')
                issues.extend(labor['issues'])
            except (tcp_labor.InspectionError, ValueError):
                issues.append('TCP labor request failed; no estimate available.')
        print(json.dumps({'report_date': day, 'ready': not issues, 'issues': issues}, indent=2))
        return 1 if issues else 0
    target = date.fromisoformat(day)
    window = {'start': (target - timedelta(days=args.catch_up_days)).isoformat(),
              'end': (target - timedelta(days=1)).isoformat()} if args.catch_up_days else None

    def catch_up():
        """Fill blank cells across the window; returns a problem description or None."""
        if not window:
            return None
        from repair_workbooks import repair
        try:
            update_run_status(phase='earlier workbook recovery', recovery={'status': 'running', **window})
            repair(window['start'], window['end'], config, gaps_only=True)
            update_run_status(recovery={'status': 'complete', **window})
            return None
        except Exception as exc:
            # Older source outages never block or hide today's result. The overall
            # run still fails, preserving a visible recovery alert.
            problem = str(exc) if isinstance(exc, (DriveError, WorkbookError,
                altametrics.AltametricsError, ValueError)) else type(exc).__name__
            update_run_status(recovery={'status': 'failed', **window, **safe_failure(exc)})
            print('Earlier workbook gaps remain unresolved: ' + problem, flush=True)
            return problem

    if window:
        # Recorded before the current report, so a portal block that stops run()
        # leaves a truthful status: catch-up was requested but not attempted.
        update_run_status(recovery={'status': 'not_attempted', **window})
    # The current report runs first. A blocked portal then stops after one
    # login attempt per region instead of repeating it for the catch-up window,
    # and an already-delivered date still lets later slots recover older gaps.
    try:
        run(day, config, args.preview, reviewed)
    except altametrics.AltametricsError:
        raise  # The same portal block would stop the catch-up logins too.
    except Exception:
        catch_up()  # Non-portal failures still let older gaps recover now.
        raise
    catch_up_problem = catch_up()
    if window and not args.preview:
        record_catch_up(day, 'failed' if catch_up_problem else 'complete')
    if catch_up_problem:
        raise ReportError('Current report completed, but earlier workbook gaps need attention: ' + catch_up_problem)
    update_run_status(complete=True, phase='complete')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ReportError, DriveError, WorkbookError, tcp_labor.InspectionError, altametrics.AltametricsError, ValueError) as exc:
        record_failure(exc)
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        record_failure(exc)
        # Do not let transport tracebacks print tokens, signed links, or records.
        print('Unexpected failure (' + type(exc).__name__ + '); no success is assumed.', file=sys.stderr)
        sys.exit(1)
