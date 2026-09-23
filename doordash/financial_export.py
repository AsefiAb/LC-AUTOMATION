"""Reconcile official DoorDash financial exports with tracked orders, without billing.

Financial adjustments with no reason are not automatically dispute recoveries.
This read-only import deliberately cannot change the live ledger or send email.
"""
import argparse
import csv
import hashlib
import io
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit
import zipfile


FIELDS = ['Timestamp local time', 'Payout date', 'Store ID', 'Transaction type',
          'Delivery UUID', 'DoorDash transaction ID', 'Description', 'Error charges', 'Adjustments']


def cents(value):
    if value in ('', 'NULL'):
        return None
    try:
        number = Decimal(value)
        if not number.is_finite() or number * 100 != (number * 100).to_integral_value():
            raise ValueError('Invalid financial amount')
        return int(number * 100)
    except InvalidOperation:
        raise ValueError('Invalid financial amount') from None


def read_exports(paths):
    """Merge overlapping downloads by immutable transaction ID; reject conflicts."""
    transactions, sources = {}, []
    duplicates = 0
    for path in paths:
        raw = Path(path).read_bytes()
        sources.append({'filename': Path(path).name, 'sha256': hashlib.sha256(raw).hexdigest()})
        if zipfile.is_zipfile(io.BytesIO(raw)):
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = [n for n in archive.namelist()
                           if Path(n).name.startswith('FINANCIAL_ERROR_CHARGES_AND_ADJUSTMENTS_')
                           and n.lower().endswith('.csv')]
                if not members:
                    raise ValueError('ZIP has no financial error charges and adjustments CSV')
                contents = [archive.read(n) for n in members]
        else:
            contents = [raw]
        for content in contents:
            reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
            if not set(FIELDS).issubset(reader.fieldnames or []):
                raise ValueError('Unexpected DoorDash financial export columns')
            for row in reader:
                record = {k: row[k] for k in FIELDS}
                if not record['DoorDash transaction ID'] or record['DoorDash transaction ID'] == 'NULL':
                    raise ValueError('Missing financial transaction ID')
                if record['Transaction type'] not in ('Error Charge', 'Adjustment'):
                    raise ValueError('Unknown financial transaction type')
                record['charge_cents'] = cents(record['Error charges'])
                record['adjustment_cents'] = cents(record['Adjustments'])
                key = record['DoorDash transaction ID']
                if key in transactions:
                    if transactions[key] != record:
                        raise ValueError('Conflicting financial records for the same transaction')
                    duplicates += 1
                else:
                    transactions[key] = record
    return list(transactions.values()), sources, duplicates


def reconcile(rows, ledger):
    if ledger.get('version') != 1 or not isinstance(ledger.get('orders'), dict):
        raise ValueError('Invalid historical ledger')
    groups = defaultdict(list)
    for row in rows:
        groups[(row['Store ID'], row['Delivery UUID'])].append(row)
    tracked_stores, seen, results = set(), set(), []
    for entry in ledger['orders'].values():
        url = urlsplit(entry['href'])
        prefix = '/merchant/financials/transactions/'
        if url.scheme != 'https' or url.hostname != 'www.doordash.com' or not url.path.startswith(prefix):
            raise ValueError('Unexpected tracked order URL')
        delivery = url.path[len(prefix):].strip('/')
        if not delivery or '/' in delivery:
            raise ValueError('Invalid tracked delivery ID')
        store = str(entry['storeId'])
        key = (store, delivery)
        if key in seen:
            raise ValueError('Duplicate tracked store and delivery ID')
        seen.add(key)
        tracked_stores.add(store)
        matches = groups[key]
        adjustments = [r for r in matches if r['Transaction type'] == 'Adjustment']
        issues = []
        if not matches:
            issues.append('Tracked order is outside or missing from this export')
        if any(r['adjustment_cents'] is None for r in adjustments):
            issues.append('Adjustment amount missing; never assume zero or a full refund')
        net = sum(r['adjustment_cents'] for r in adjustments if r['adjustment_cents'] is not None)
        if adjustments:
            issues.append('Confirm adjustment reason and dispute outcome before including in AB earnings')
        else:
            issues.append('No financial adjustment in this export; not proof of denial')
        results.append({'store_id': store, 'store': entry.get('store', ''),
                        'delivery_uuid': delivery, 'ledger_status': entry.get('status'),
                        'matched': bool(matches), 'adjustment_count': len(adjustments),
                        'known_net_adjustment_cents': net,
                        'adjustment_amount_complete': all(r['adjustment_cents'] is not None for r in adjustments),
                        'transactions': matches, 'issues': issues})
    return {'version': 1, 'billing_ready': False,
            'basis': 'Linked financial adjustments require dispute-recovery confirmation; no AB invoice generated.',
            'tracked_orders': len(results), 'matched_orders': sum(r['matched'] for r in results),
            'orders_with_adjustments': sum(r['adjustment_count'] > 0 for r in results),
            'known_net_adjustment_cents': sum(r['known_net_adjustment_cents'] for r in results),
            'other_store_ids': sorted({r['Store ID'] for r in rows} - tracked_stores),
            'unmatched_transaction_count': sum((r['Store ID'], r['Delivery UUID']) not in seen for r in rows),
            'orders': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', action='append', required=True, dest='exports')
    parser.add_argument('--ledger', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    rows, sources, duplicates = read_exports(args.exports)
    result = reconcile(rows, json.loads(Path(args.ledger).read_text(encoding='utf-8')))
    result.update(sources=sources, transaction_count=len(rows), duplicate_transactions_removed=duplicates)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f"Matched {result['matched_orders']} of {result['tracked_orders']} tracked orders; "
          'private reconciliation saved. Billing remains blocked.')


if __name__ == '__main__':
    main()
