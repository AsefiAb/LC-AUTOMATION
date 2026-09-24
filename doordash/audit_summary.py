"""Reconcile a dated, read-only portal audit with official financial transactions."""
import argparse
import base64
from collections import Counter
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import time
import requests
from financial_export import cents


def summarize(audit):
    start, end = audit['start'], audit['end']
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError('Invalid audit dates')
    seen, details, exceptions, scope_count = set(), [], [], 0
    totals = {'charged_cents': 0, 'known_adjustment_cents': 0, 'confirmed_credit_cents': 0,
              'unconfirmed_positive_adjustment_cents': 0}
    stores = {key: {**value, 'store_id': key, 'orders': 0, 'status_counts': Counter(),
                    'charged_cents': 0, 'confirmed_credit_cents': 0, 'issues': 0}
              for key, value in audit['stores'].items()}
    for key, order in audit['orders'].items():
        sid, delivery = order['store_id'], order['delivery_uuid']
        if key != sid + ':' + delivery or sid not in stores:
            raise ValueError('Audit order identity mismatch')
        scope, reasons = [], []
        for tx in order['transactions']:
            if tx['Store ID'] != sid or tx['Delivery UUID'] != delivery:
                raise ValueError('Financial transaction linked to the wrong order or store')
            transaction_id = tx['DoorDash transaction ID']
            if transaction_id in seen:
                raise ValueError('Duplicate transaction in audit')
            seen.add(transaction_id)
            if tx['charge_cents'] != cents(tx['Error charges']) or tx['adjustment_cents'] != cents(tx['Adjustments']):
                raise ValueError('Stored amount does not match original financial export')
            if start <= tx['Timestamp local time'][:10] <= end:
                scope.append(tx)
        if not scope:
            raise ValueError('Audit contains an order outside the requested dates')
        scope_count += len(scope)
        status = order['portal']['status']
        raw_status = order['portal'].get('raw_status')
        known = {'DISPUTE_STATUS_REFUNDED': 'approved', 'DISPUTE_STATUS_DENIED': 'denied',
                 'DISPUTE_STATUS_PENDING': 'pending'}
        if raw_status == 'DISPUTE_STATUS_CREATED' and order['portal'].get('ui_verified') and 'Dispute charge' in order['portal'].get('ui_evidence', ''):
            known['DISPUTE_STATUS_CREATED'] = 'not_disputed'
        if raw_status and status != known.get(raw_status, 'unknown'):
            raise ValueError('Stored portal status does not match the source response')
        if status not in ('approved', 'denied', 'pending', 'not_disputed'):
            reasons.append('Portal outcome is unverified')
        if status == 'not_disputed':
            reasons.append('Not submitted; portal deadline has passed' if order['portal'].get('deadline_passed')
                           else 'Not submitted; review evidence and portal eligibility')
        charge = -sum(t['charge_cents'] or 0 for t in scope if t['Transaction type'] == 'Error Charge')
        lifetime_charge = -sum(t['charge_cents'] or 0 for t in order['transactions'] if t['Transaction type'] == 'Error Charge')
        adjustments = [t for t in scope if t['Transaction type'] == 'Adjustment']
        net = sum(t['adjustment_cents'] for t in adjustments if t['adjustment_cents'] is not None)
        if any(t['adjustment_cents'] is None for t in adjustments):
            reasons.append('Financial adjustment amount is missing')
        if any(t['charge_cents'] is None for t in scope if t['Transaction type'] == 'Error Charge'):
            reasons.append('Financial error-charge amount is missing')
        full_net = sum(t['adjustment_cents'] or 0 for t in order['transactions'] if t['Transaction type'] == 'Adjustment')
        if net > 0 and status != 'approved':
            reasons.append('Positive financial adjustment without a confirmed refunded dispute status')
        if full_net > lifetime_charge or charge < 0:
            reasons.append('Credits exceed the recorded error charges; confirm adjustment purpose')
        portal_amount = order['portal'].get('approved_amount_cents')
        if status == 'approved' and portal_amount is not None and full_net > portal_amount:
            reasons.append('Financial credits exceed the amount shown by the portal')
        confirmed = net if status == 'approved' and not reasons else None
        row = {'store': stores[sid]['label'], 'store_id': sid, 'delivery_uuid': delivery,
               'status': status, 'raw_status': raw_status, 'checked_at': order['portal'].get('checked_at'),
               'charged_cents': charge, 'known_adjustment_cents': net, 'confirmed_credit_cents': confirmed,
               'issues': reasons, 'transactions': scope,
               'note': 'Approved; no financial credit recorded in this period' if status == 'approved' and not net else ''}
        details.append(row)
        for reason in reasons:
            exceptions.append({'store': row['store'], 'delivery_uuid': delivery, 'issue': reason})
        store = stores[sid]
        store['orders'] += 1; store['status_counts'][status] += 1
        store['charged_cents'] += charge; store['confirmed_credit_cents'] += confirmed or 0
        store['issues'] += bool(reasons)
        totals['charged_cents'] += charge; totals['known_adjustment_cents'] += net
        totals['confirmed_credit_cents'] += confirmed or 0
        if confirmed is None: totals['unconfirmed_positive_adjustment_cents'] += max(net, 0)
    if scope_count != audit['transaction_count']:
        raise ValueError('Audit does not account for every financial transaction in the date range')
    complete = audit['complete'] is True and len(details) == audit['expected_orders']
    if not complete:
        exceptions.append({'store': 'All stores', 'delivery_uuid': '', 'issue': 'Portal sweep is incomplete'})
    status_counts = Counter(r['status'] for r in details)
    return {'version': 1, 'start': start, 'end': end, 'complete': complete, 'billing_ready': False,
            'scope': 'DoorDash only; financial transactions posted during these inclusive dates. Portal statuses were checked later, at the listed times. Not bank-deposit confirmation or an all-time invoice.',
            'expected_orders': audit['expected_orders'], 'checked_orders': len(details), 'transaction_count': scope_count,
            'status_counts': dict(status_counts), 'totals': totals,
            'ab_share_on_confirmed_credits_cents': (totals['confirmed_credit_cents'] * 15 + 50) // 100,
            'sources': audit['sources'], 'stores': list(stores.values()), 'orders': details, 'issues': exceptions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input')
    parser.add_argument('--cloud-period')
    parser.add_argument('--output', required=True)
    parser.add_argument('--refresh-status', action='store_true')
    args = parser.parse_args()
    if bool(args.input) == bool(args.cloud_period):
        raise ValueError('Choose a local audit or a private cloud audit period')
    if args.cloud_period:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}', args.cloud_period):
            raise ValueError('Invalid audit period identifier')
        response = requests.get('https://api.github.com/repos/AsefiAb/LC-AUTOMATION/contents/audits/' + args.cloud_period + '.json',
            params={'ref': 'doordash-state'}, headers={'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
            'Accept': 'application/vnd.github+json'}, timeout=40)
        response.raise_for_status()
        audit = json.loads(base64.b64decode(response.json()['content']))
        if args.cloud_period != audit['start'] + '_' + audit['end']:
            raise ValueError('Requested audit period differs from saved source')
    else:
        audit = json.loads(Path(args.input).read_text())
    if args.refresh_status:
        from cloud_status_check import status_client, read_status
        client = status_client()
        for n, order in enumerate(audit['orders'].values(), 1):
            value = read_status(client, order['delivery_uuid'])
            raw = value.get('disputeStatus')
            status = {'DISPUTE_STATUS_REFUNDED': 'approved', 'DISPUTE_STATUS_DENIED': 'denied',
                      'DISPUTE_STATUS_PENDING': 'pending'}.get(raw, 'unknown')
            if raw == 'DISPUTE_STATUS_CREATED' and order['portal'].get('ui_verified'):
                status = 'not_disputed'
            order['portal'].update(status=status, raw_status=raw, approved_amount_cents=None,
                checked_at=datetime.now(timezone.utc).isoformat(),
                evidence='Fresh authenticated GitHub cloud read of the merchant dispute-status service')
            if n % 50 == 0: print(f'GitHub refreshed {n}/{len(audit["orders"])} order statuses', flush=True)
            time.sleep(.3)
        audit['cloud_refreshed_at'] = datetime.now(timezone.utc).isoformat()
    summary = summarize(audit)
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    if args.refresh_status:
        (out.parent / 'source-audit.json').write_text(json.dumps(audit), encoding='utf-8')
    out.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(f"Audit reconciled {summary['checked_orders']} orders and {summary['transaction_count']} financial transactions; no email sent.")


if __name__ == '__main__':
    main()
