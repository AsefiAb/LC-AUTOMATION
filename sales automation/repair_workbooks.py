"""Reconcile dated OneDrive sales inputs against fresh Altametrics reports.

No payroll changes or email delivery. Every upload keeps a before copy and a
cell-level audit, uses an eTag precondition, then reads the changed cells back.
"""
import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
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


def repair(start, end, config, *, preview=False, gaps_only=False):
    if config.get('source') != 'Altametrics':
        raise ValueError('Workbook recovery requires the configured Altametrics source.')
    dates = dates_between(start, end, config['timezone'])
    output = FOLDER / 'reports' / ('workbook-repair-' + start + '_' + end)
    output.mkdir(parents=True, exist_ok=True)
    audit = {'start': start, 'end': end, 'preview': preview, 'gaps_only': gaps_only,
             'started_at': datetime.now(timezone.utc).isoformat(), 'complete': False,
             'source': 'Fresh Altametrics Product Net and Actual Total transactions',
             'emails_sent': 0, 'workbooks': [], 'dates_read': []}

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
        audit.update(complete=True, result='No blank dated sales/transaction inputs found.')
        save_audit()
        print(audit['result'], flush=True)
        return audit

    raw = altametrics.fetch_many(sorted(needed_dates), config)
    if any(record.get('date') not in needed_dates for record in raw):
        raise ValueError('Source contains a date outside this recovery request.')
    by_day = {day: altametrics.validate(day, [r for r in raw if r.get('date') == day], config)
              for day in sorted(needed_dates)}
    (output / 'source-records.json').write_text(json.dumps(by_day, indent=2), encoding='utf-8')
    audit['dates_read'] = sorted(needed_dates)

    # All regions and dates must validate before the first upload.
    uploads = []
    for book, snapshot, stores, entry in prepared:
        content = snapshot['content']
        for day, rows in by_day.items():
            content, changes = update(content, [r for r in rows if r['region'] == book['region']], day,
                sales_whole_dollars=config.get('workbook_sales_rounding') == 'whole_dollars',
                replace_conflicts=not gaps_only, only_blank=gaps_only)
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
    audit.update(complete=True, finished_at=datetime.now(timezone.utc).isoformat())
    save_audit()
    print(('Preview prepared' if preview else 'Workbook recovery verified') + ': '
          + str(sum(len(e['changes']) for e in audit['workbooks'])) + ' dated cells; no email sent.', flush=True)
    return audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--gaps-only', action='store_true')
    args = parser.parse_args()
    config = json.loads((FOLDER / 'config.json').read_text())
    repair(args.start, args.end, config, preview=args.preview, gaps_only=args.gaps_only)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (DriveError, WorkbookError, altametrics.AltametricsError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print('Workbook recovery failed (' + type(exc).__name__ + '); inspect the private audit.', file=sys.stderr)
        sys.exit(1)
