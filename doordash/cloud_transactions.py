"""Verify every dated financial transaction directly against the saved official export."""
from collections import Counter
from datetime import datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import time as clock
from zoneinfo import ZoneInfo
from cloud_status_check import status_client
from financial_export import cents

ZONES = {'CO': 'America/Denver', 'AZ': 'America/Phoenix', 'TX': 'America/Chicago'}


def key(row):
    return (row['store_id'], row['delivery_uuid'], row['date'], row['type'].casefold(), row['charge_cents'], row['adjustment_cents'])


def collect(client, store_id, region, start, end):
    zone = ZoneInfo(ZONES[region])
    first = datetime.combine(datetime.fromisoformat(start).date(), time.min, zone).astimezone(timezone.utc)
    last = (datetime.combine(datetime.fromisoformat(end).date() + timedelta(days=1), time.min, zone)
            - timedelta(milliseconds=1)).astimezone(timezone.utc)
    params = {'entityType': 'store', 'storeId': store_id, 'businessId': '41933',
              'startTime': first.isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
              'endTime': last.isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
              'transactionTypes': 'Error Charge,Adjustment', 'limit': 20, 'offset': 0}
    rows, seen, expected = [], set(), None
    while True:
        response = client.get('https://merchant-portal.doordash.com/merchant-analytics-service/api/v1/transactions', params=params, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f'Financial source returned HTTP {response.status_code}; store scan incomplete')
        data = response.json()
        total = data.get('totalCount')
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            raise ValueError('Missing financial total count')
        if expected is None: expected = total
        if total != expected: raise ValueError('Financial count changed during pagination')
        page = data['transactions']
        if len(page) != min(20, expected - len(rows)):
            raise ValueError('Financial pagination returned an incomplete or oversized page')
        headers = {column['displayString']: column['name'] for column in data['columns']}
        for cells in page:
            values = {cell['name']: cell for cell in cells}
            field = lambda name: values[headers[name]]['value']
            identity = values[headers['Order ID']]['action']['payload']['data']
            if str(identity['storeId']) != store_id: raise ValueError('Financial row belongs to another store')
            txid = field('Transaction ID')
            if not txid or txid in seen: raise ValueError('Repeated or missing financial transaction ID')
            seen.add(txid)
            kind = field('Transaction type')
            if kind.casefold() not in ('error charge', 'adjustment'): raise ValueError('Financial type filter was not applied')
            local_date = datetime.strptime(field('Date'), '%m/%d/%Y').date().isoformat()
            if not start <= local_date <= end: raise ValueError('Financial row outside requested local dates')
            def money(name):
                value = field(name).replace('$', '').replace(',', '')
                return cents('NULL' if value in ('', '--') else value)
            rows.append({'store_id': store_id, 'delivery_uuid': identity['deliveryUuid'], 'transaction_id': txid,
                         'date': local_date, 'type': kind, 'charge_cents': money('Error charges'), 'adjustment_cents': money('Adjustments')})
        if len(rows) == expected: return rows
        params['offset'] = len(rows)
        if params['offset'] >= 10000: raise ValueError('Financial pagination limit reached; scan incomplete')
        clock.sleep(.2)


def verify(audit, client):
    actual, coverage = [], []
    for sid, store in audit['stores'].items():
        rows = collect(client, sid, store['region'], audit['start'], audit['end'])
        actual.extend(rows)
        coverage.append({'store': store['label'], 'store_id': sid, 'transactions': len(rows), 'complete': True})
        print(f"Financial source verified pages for {store['label']}: {len(rows)} transactions", flush=True)
    expected = []
    for order in audit['orders'].values():
        for tx in order['transactions']:
            if audit['start'] <= tx['Timestamp local time'][:10] <= audit['end']:
                expected.append({'store_id': order['store_id'], 'delivery_uuid': order['delivery_uuid'],
                    'date': tx['Timestamp local time'][:10], 'type': tx['Transaction type'],
                    'charge_cents': tx['charge_cents'], 'adjustment_cents': tx['adjustment_cents']})
    left, right = Counter(map(key, expected)), Counter(map(key, actual))
    return {'start': audit['start'], 'end': audit['end'], 'verified_at': datetime.now(timezone.utc).isoformat(),
            'complete': left == right, 'export_transactions': len(expected), 'cloud_transactions': len(actual),
            'missing_from_cloud': sum((left-right).values()), 'not_in_export': sum((right-left).values()), 'stores': coverage}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = verify(json.loads(Path(args.audit).read_text()), status_client())
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding='utf-8')
    if not result['complete']: raise ValueError('Current cloud finances differ from the export; review the differences before relying on the workbook')
    print('Every financial transaction matches the official export across all stores.')
