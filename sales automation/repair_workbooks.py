"""Reconcile dated OneDrive sales inputs against fresh Altametrics reports.

No payroll changes or email delivery. Every upload keeps a before copy and a
cell-level audit, uses an eTag precondition, then reads the changed cells back.
"""
import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import altametrics
from onedrive import OneDrive, DriveError
from workbooks import WorkbookError, input_cells, update

FOLDER = Path(__file__).resolve().parent


def dates_between(start, end, timezone_name):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or (last - first).days > 30:
        raise ValueError('Recovery requires 1 to 31 consecutive dates.')
    if last >= datetime.now(ZoneInfo(timezone_name)).date():
        raise ValueError('Recovery must use completed business dates before today.')
    return [(first + timedelta(days=n)).isoformat() for n in range((last-first).days+1)]


def reviewed_source(path, expected_sha256, start, end, config):
    """Check an explicitly reviewed browser extract without inventing labor data.

    The digest binds the GitHub dispatch to the reviewed private payload. It is
    an integrity check, not independent confirmation of the portal's figures.
    """
    if not isinstance(expected_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
        raise ValueError('Reviewed source requires its exact lowercase SHA-256 digest.')
    content = Path(path).read_bytes()
    if len(content) > 250_000:
        raise ValueError('Reviewed sales source exceeds the supported size.')
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError('Reviewed source digest does not match this dispatch.')
    payload = json.loads(content)
    required_fields = {'schema_version', 'start', 'end', 'regions', 'provenance', 'records'}
    if (not isinstance(payload, dict) or not required_fields <= set(payload)
            or not set(payload) <= required_fields | {'unavailable'}):
        raise ValueError('Reviewed source schema is incomplete or has unexpected fields.')
    if type(payload['schema_version']) is not int or payload['schema_version'] != 1:
        raise ValueError('Unsupported reviewed source schema version.')
    if payload['start'] != start or payload['end'] != end:
        raise ValueError('Reviewed source dates do not match this recovery request.')
    days = dates_between(start, end, config['timezone'])
    regions = payload['regions']
    known = {b['region'] for b in config['workbooks']}
    if (not isinstance(regions, list) or not regions or any(type(r) is not str for r in regions)
            or len(set(regions)) != len(regions) or not set(regions) <= known):
        raise ValueError('Reviewed source must select unique configured regions.')
    provenance = payload['provenance']
    required = {'system': 'Altametrics', 'report': 'Daily Activity Report',
                'sales_metric': 'Product Net', 'transactions_metric': 'Actual Total transactions',
                'method': 'authenticated_browser_review'}
    if (not isinstance(provenance, dict) or set(provenance) != set(required) | {
            'observed_at', 'reviewed_by', 'evidence'}
            or any(provenance.get(k) != v for k, v in required.items())):
        raise ValueError('Reviewed source must identify the observed report and exact sales/transaction metrics.')
    def label(value):
        return isinstance(value, str) and 0 < len(value.strip()) <= 512 and not any(ord(c) < 32 for c in value)
    if not label(provenance['reviewed_by']):
        raise ValueError('Reviewed source must identify its reviewer.')
    if (not isinstance(provenance['evidence'], list) or not provenance['evidence']
            or not all(label(value) and '://' not in value for value in provenance['evidence'])):
        raise ValueError('Reviewed source requires saved evidence file references; do not include session URLs.')
    observed = datetime.fromisoformat(str(provenance['observed_at']).replace('Z', '+00:00'))
    if observed.tzinfo is None or observed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError('Reviewed source observation time must be timezone-aware and not in the future.')
    if observed.astimezone(ZoneInfo(config['timezone'])).date() <= date.fromisoformat(end):
        raise ValueError('Reviewed source must have been observed after the requested report period ended.')
    selected = copy.deepcopy(config)
    selected['stores'] = [s for s in config['stores'] if s['region'] in regions]
    selected['workbooks'] = [b for b in config['workbooks'] if b['region'] in regions]
    stores = {s['store']: s for s in selected['stores']}
    expected = {(day, store) for day in days for store in stores}
    if not isinstance(payload['records'], list):
        raise ValueError('Reviewed source records must be a list.')
    by_day = {day: [] for day in days}
    seen = set()
    for record in payload['records']:
        if not isinstance(record, dict) or set(record) != {'date', 'store', 'sales', 'transactions'}:
            raise ValueError('Reviewed records must contain only date, store, sales and transactions.')
        if type(record['date']) is not str or type(record['store']) is not str:
            raise ValueError('Reviewed source date and store must be strings.')
        key = (record['date'], record['store'])
        if key not in expected or key in seen:
            raise ValueError('Reviewed source has a duplicate, unknown store or out-of-range date.')
        sales_text = str(record['sales'])
        transactions_text = str(record['transactions'])
        if (type(record['sales']) not in (str, int, float)
                or not re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?', sales_text)):
            raise ValueError('Reviewed sales must be a canonical nonnegative decimal with at most two decimals; no currency signs or separators.')
        if (type(record['transactions']) not in (str, int)
                or not re.fullmatch(r'(?:0|[1-9][0-9]*)', transactions_text)):
            raise ValueError('Reviewed transactions must be canonical whole unsigned counts; no currency signs, separators or decimals.')
        sales, transactions = Decimal(sales_text), int(transactions_text)
        seen.add(key)
        by_day[key[0]].append({**stores[key[1]], 'report_date': key[0],
                              'sales': f'{sales:.2f}', 'transactions': int(transactions)})
    unavailable = payload.get('unavailable', [])
    if not isinstance(unavailable, list):
        raise ValueError('Explicit unavailable sources must be a list.')
    for item in unavailable:
        if (not isinstance(item, dict) or set(item) != {'date', 'store', 'reason'}
                or type(item['date']) is not str or type(item['store']) is not str
                or not label(item['reason']) or '://' in item['reason']):
            raise ValueError('Unavailable source requires an exact date, store and safe observed reason.')
        key = (item['date'], item['store'])
        if key not in expected or key in seen:
            raise ValueError('Unavailable source duplicates a record or contains an unknown store/date.')
        seen.add(key)
    if seen != expected:
        raise ValueError('Reviewed source must cover every selected store/date with a record or explicit unavailable reason.')
    return selected, by_day, payload


def repair(start, end, config, *, preview=False, gaps_only=False,
           source_records=None, source_sha256=None):
    if config.get('source') != 'Altametrics':
        raise ValueError('Workbook recovery requires the configured Altametrics source.')
    dates = dates_between(start, end, config['timezone'])
    reviewed = reviewed_rows = None
    if source_records:
        config, reviewed_rows, reviewed = reviewed_source(source_records, source_sha256, start, end, config)
    elif source_sha256:
        raise ValueError('A source digest requires an explicit reviewed source file.')
    unavailable = reviewed.get('unavailable', []) if reviewed else []
    output = FOLDER / 'reports' / ('workbook-repair-' + start + '_' + end)
    output.mkdir(parents=True, exist_ok=True)
    audit = {'start': start, 'end': end, 'preview': preview, 'gaps_only': gaps_only,
             'started_at': datetime.now(timezone.utc).isoformat(), 'complete': False,
             'source': 'Fresh Altametrics Product Net and Actual Total transactions',
             'emails_sent': 0, 'workbooks': [], 'dates_read': []}
    if reviewed:
        audit.update(source='Reviewed Altametrics browser records',
                     source_sha256=source_sha256, provenance=reviewed['provenance'],
                     selected_regions=reviewed['regions'], unavailable=unavailable,
                     unverified_cells=[])
        (output / 'reviewed-source.json').write_text(json.dumps(reviewed, indent=2), encoding='utf-8')

    def save_audit():
        (output / 'repair-audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')

    save_audit()
    drive = OneDrive()
    prepared = []
    needed_dates = set()
    # Inspect every destination before fetching any source or changing anything.
    for book in config['workbooks']:
        snapshot = drive.read(book['url'])
        region = book['region']
        (output / (region + '.before.xlsx')).write_bytes(snapshot['content'])
        stores = [s['store'] for s in config['stores'] if s['region'] == region]
        for day in dates:
            cells = input_cells(snapshot['content'], stores, day)
            missing_stores = {item['store'] for item in unavailable if item['date'] == day}
            if reviewed:
                audit['unverified_cells'].extend({**cell, 'date': day, 'region': region}
                    for cell in cells if cell['store'] in missing_stores)
            if any(c['formula'] for c in cells):
                raise WorkbookError('Recovery destination contains a formula: ' + region + ' ' + day)
            if not gaps_only or any(c['value'] == '' for c in cells):
                needed_dates.add(day)
        entry = {'region': region, 'before_sha256': hashlib.sha256(snapshot['content']).hexdigest(),
                 'changes': [], 'uploaded': False, 'verified': False}
        audit['workbooks'].append(entry)
        prepared.append((book, snapshot, stores, entry))
    save_audit()
    if not needed_dates:
        audit.update(complete=not unavailable, result='No blank dated sales/transaction inputs found.',
                     available_records_verified=True)
        save_audit()
        print(audit['result'], flush=True)
        return audit

    if reviewed_rows is not None:
        by_day = {day: reviewed_rows[day] for day in sorted(needed_dates)}
    else:
        raw = altametrics.fetch_many(sorted(needed_dates), config)
        if any(record.get('date') not in needed_dates for record in raw):
            raise ValueError('Source contains a date outside this recovery request.')
        by_day = {day: altametrics.validate(day, [r for r in raw if r.get('date') == day], config)
                  for day in sorted(needed_dates)}
    (output / 'source-records.json').write_text(json.dumps(by_day, indent=2), encoding='utf-8')
    audit['dates_read'] = sorted(needed_dates)
    audit['available_record_count'] = sum(len(rows) for rows in by_day.values())
    audit['unavailable_record_count'] = len(unavailable)

    # All regions and dates must validate before the first upload.
    uploads = []
    for book, snapshot, stores, entry in prepared:
        content = snapshot['content']
        for day, rows in by_day.items():
            region_rows = [r for r in rows if r['region'] == book['region']]
            if not region_rows:
                continue  # All stores for this region/date explicitly unavailable.
            content, changes = update(content, region_rows, day,
                sales_whole_dollars=config.get('workbook_sales_rounding') == 'whole_dollars',
                replace_conflicts=not gaps_only, only_blank=gaps_only, store_layout=stores)
            entry['changes'].extend({**change, 'date': day} for change in changes)
        entry['proposed_sha256'] = hashlib.sha256(content).hexdigest()
        (output / (book['region'] + '.after.xlsx')).write_bytes(content)
        uploads.append((book, snapshot, stores, entry, content))
    save_audit()

    for book, snapshot, stores, entry, content in uploads:
        if preview:
            continue
        if entry['changes']:
            drive.write(snapshot, content)
            entry['uploaded'] = True
            save_audit()
            verified = drive.read(book['url'])
            actual = {(day, cell['sheet'], cell['cell']): cell for day in by_day
                      for cell in input_cells(verified['content'], stores, day)}
            from decimal import Decimal
            for change in entry['changes']:
                cell = actual[(change['date'], change['sheet'], change['cell'])]
                if cell['formula'] or Decimal(cell['value']) != Decimal(change['value']):
                    raise WorkbookError('Uploaded recovery did not retain an expected input.')
            (output / (book['region'] + '.after.xlsx')).write_bytes(verified['content'])
        entry['verified'] = True
        save_audit()
    audit.update(complete=not unavailable, available_records_verified=not preview,
                 finished_at=datetime.now(timezone.utc).isoformat())
    save_audit()
    print(('Preview prepared' if preview else ('Available workbook corrections verified' if unavailable else 'Workbook recovery verified')) + ': '
          + str(sum(len(e['changes']) for e in audit['workbooks'])) + ' dated cells; no email sent.', flush=True)
    if unavailable:
        print(f'Incomplete recovery: {len(unavailable)} explicit store/date sources remain unavailable; those cells were preserved.', flush=True)
    return audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--gaps-only', action='store_true')
    parser.add_argument('--source-records', help='Explicitly reviewed browser-source JSON file; never inferred automatically')
    parser.add_argument('--source-sha256', help='Exact SHA-256 of the reviewed source file')
    args = parser.parse_args()
    config = json.loads((FOLDER / 'config.json').read_text())
    result = repair(args.start, args.end, config, preview=args.preview, gaps_only=args.gaps_only,
                    source_records=args.source_records, source_sha256=args.source_sha256)
    return 0 if result['complete'] else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (DriveError, WorkbookError, altametrics.AltametricsError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print('Workbook recovery failed (' + type(exc).__name__ + '); inspect the private audit.', file=sys.stderr)
        sys.exit(1)
