"""Read the legacy multi-platform tracker without double-counting its subtotals.

Snapshots are retained as historical evidence, never added to live order ledgers
or treated as a current-period/all-time reconciliation.
"""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re


def cents(value):
    if not isinstance(value, (int, float, Decimal)) or isinstance(value, bool):
        raise ValueError('A monetary cell is missing or not numeric.')
    number = Decimal(str(value))
    if not number.is_finite() or number < 0: raise ValueError('Invalid tracker amount')
    return int((number * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def account(label):
    if re.match(r'^Uber(?: Eats)? \(acct (\d+)\)$', label):
        return 'Uber Eats account ' + re.search(r'acct (\d+)', label)[1]
    if label.startswith('Grubhub'): return 'Grubhub'
    return label


def platform(label):
    return 'Uber Eats' if label.startswith('Uber Eats account ') else label


def parse_tables(tables, source_name, sha256):
    summary, detail, weekly = tables['Summary'], tables['By store'], tables['By week']
    header = next(i for i, row in enumerate(summary) if row[0] == 'Platform')
    if list(summary[header][:9]) != ['Platform', 'Stores', 'Charges', 'Charged back ($)', 'Approved (#)', 'Denied (#)', 'Pending (#)', 'Recovered ($)', 'Denied ($)']:
        raise ValueError('Tracker Summary columns changed; inspect before importing.')
    generated = next((re.search(r'Generated (\S+)', str(row[0]))[1] for row in summary if row[0] and re.search(r'Generated (\S+)', str(row[0]))), None)
    if not generated: raise ValueError('Tracker generation time is missing.')
    datetime.fromisoformat(generated.replace('Z', '+00:00'))
    if list(detail[0][:10]) != ['Platform', 'Store', 'Charges', 'Charged back ($)', 'Approved (#)', 'Denied (#)', 'Pending (#)', 'Recovered ($)', 'Denied ($)', 'Pending ($)']:
        raise ValueError('Tracker store columns changed; inspect before importing.')
    accounts, stores, issues = [], [], []
    for index, row in enumerate(summary[header + 1:], start=header + 2):
        label = row[0]
        if not label or label.startswith(('TOTAL', 'Generated')) or '(both accts)' in label: continue
        key = account(label)
        if key in {a['account'] for a in accounts}: raise ValueError('Duplicate tracker account')
        accounts.append({'account': key, 'platform': platform(key), 'source_row': index,
            'stores_reported': row[1], 'charges': row[2], 'charged_cents': cents(row[3]),
            'approved_count': row[4], 'denied_count': row[5], 'pending_count': row[6],
            'recovered_cents': cents(row[7]), 'denied_cents': cents(row[8])})
        if row[1] == 0:
            issues.append(f'{key}: zero stores returned; zero recovery does not establish complete account coverage.')
    for index, row in enumerate(detail[1:], start=2):
        if not row[0]: continue
        key = account(row[0])
        stores.append({'account': key, 'platform': platform(key), 'store': row[1], 'source_row': index,
            'charges': row[2], 'charged_cents': cents(row[3]), 'approved_count': row[4],
            'denied_count': row[5], 'pending_count': row[6], 'recovered_cents': cents(row[7]),
            'denied_cents': cents(row[8]), 'pending_cents': cents(row[9])})
    for row in accounts:
        children = [r for r in stores if r['account'] == row['account']]
        for field in ('recovered_cents', 'denied_cents', 'charged_cents', 'approved_count', 'denied_count', 'pending_count'):
            if sum(r[field] for r in children) != row[field]: raise ValueError(f'Tracker account/store totals disagree for {row["account"]}: {field}')
    if {r['account'] for r in stores} - {r['account'] for r in accounts}: raise ValueError('Store data contains an account missing from Summary.')
    groups = defaultdict(lambda: {f: 0 for f in ('recovered_cents', 'denied_cents', 'charged_cents', 'approved_count', 'denied_count', 'pending_count')})
    for row in accounts:
        for key in groups[row['platform']]: groups[row['platform']][key] += row[key]
    reported_total = sum(r['recovered_cents'] for r in accounts)
    total_row = next(r for r in summary if r[0] == 'TOTAL (all platforms)')
    if cents(total_row[7]) != reported_total: raise ValueError('Tracker platform totals do not reconcile.')
    weeks = [{'source_row': i, 'label': row[0], 'reported_recovered_cents': cents(row[6])}
             for i, row in enumerate(weekly, 1) if isinstance(row[0], str) and re.match(r'^[A-Z][a-z]{2} \d', row[0])]
    if sum(w['reported_recovered_cents'] for w in weeks) != reported_total: raise ValueError('Weekly and platform recoveries disagree.')
    issues += ['Weekly aggregates cannot be split exactly into Tuesday–Monday payroll periods without dated order/refund records.',
               'Snapshot is not a live refresh or proof of complete all-time history. Platform sources use different date scopes.',
               'Legacy report treats the original charge as recovered when a dispute is approved; partial refund amounts need reconciliation.',
               'Legacy Grubhub adapter cannot verify won/denied outcomes; its zero recovered total is not confirmation of zero savings.']
    return {'version': 1, 'source_name': source_name, 'source_sha256': sha256, 'generated_at': generated,
            'imported_at': datetime.now(timezone.utc).isoformat(), 'accounts': accounts, 'platforms': dict(groups),
            'stores': stores, 'weeks': weeks, 'reported_recovered_cents': reported_total,
            'illustrative_ab_cut_cents': (reported_total * 15 + 50) // 100,
            'live_verified': False, 'issues': issues}


def load(path):
    import openpyxl  # Read-only extraction; workbook writing is separate.
    path = Path(path)
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        tables = {name: list(book[name].values) for name in ('Summary', 'By store', 'By week')}
        return parse_tables(tables, path.name, hashlib.sha256(path.read_bytes()).hexdigest())
    finally: book.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = load(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f'Preserved {len(result["accounts"])} accounts, {len(result["stores"])} store rows, {len(result["weeks"])} weekly rows. Snapshot only; no email sent.')
