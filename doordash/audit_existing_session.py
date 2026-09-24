"""One-time read-only audit using an ALREADY OPEN authenticated Chrome tab.

Never launches a browser, submits a dispute, emails, or modifies the live ledger.
Exports and checkpoints are private and must remain in ignored report folders.
"""
import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import requests
import websocket
from financial_export import read_exports


def configured_stores():
    source = (Path(__file__).parent / 'scraper/disputes/doordash-stores.mjs').read_text()
    matches = re.findall(r'region: "([A-Z]+)", label: "([A-Z]+-\d+)", storeId: "(\d+)"', source)
    if len(matches) != 18 or len({m[2] for m in matches}) != 18:
        raise ValueError('Expected the 18 configured stores')
    return {sid: {'region': region, 'label': label} for region, label, sid in matches}


def audit_orders(rows, start, end, stores):
    scope = [r for r in rows if r['Store ID'] in stores and start <= r['Timestamp local time'][:10] <= end]
    keys = {(r['Store ID'], r['Delivery UUID']) for r in scope}
    if any(delivery in ('', 'NULL') for _, delivery in keys):
        raise ValueError('Missing delivery IDs in the audit scope')
    history = defaultdict(list)
    for row in rows:
        if (row['Store ID'], row['Delivery UUID']) in keys:
            history[(row['Store ID'], row['Delivery UUID'])].append(row)
    return scope, [{'store_id': store, 'store': stores[store]['label'], 'region': stores[store]['region'],
                    'delivery_uuid': delivery, 'transactions': history[(store, delivery)]}
                   for store, delivery in sorted(keys)]


class ExistingTab:
    def __init__(self, port):
        pages = requests.get(f'http://127.0.0.1:{port}/json/list', timeout=5).json()
        page = next((p for p in pages if p.get('type') == 'page'
                     and urlsplit(p.get('url', '')).hostname == 'www.doordash.com'), None)
        if page is None:
            raise ValueError('No existing DoorDash tab; no browser was opened')
        self.socket = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=8, suppress_origin=True)
        self.sequence = 0

    def call(self, method, params):
        self.sequence += 1
        self.socket.send(json.dumps({'id': self.sequence, 'method': method, 'params': params}))
        while True:
            value = json.loads(self.socket.recv())
            if value.get('id') == self.sequence:
                if 'error' in value:
                    raise RuntimeError('Browser audit command failed')
                return value.get('result', {})

    def outcome(self, row):
        """Read the same status endpoint observed in the authenticated portal UI.

        This reuses the existing session, not an unattended cloud connection.
        Each response must identify the requested delivery before it is trusted.
        """
        delivery = row['delivery_uuid']
        if not re.fullmatch(r'[a-fA-F0-9-]{36}', delivery):
            raise ValueError('Unexpected order identifier')
        url = 'https://merchant-portal.doordash.com/teammate-experience-service/api/v1/dispute-status/' + delivery
        expression = ('fetch(' + json.dumps(url) + ',{credentials:"include",signal:AbortSignal.timeout(25000)})'
                      '.then(async r=>({http:r.status,body:r.ok?await r.json():null}))')
        # Fetch runs within the already-authenticated browser; credentials never leave it.
        self.socket.settimeout(30)
        value = self.call('Runtime.evaluate', {'expression': expression, 'awaitPromise': True,
                           'returnByValue': True}).get('result', {}).get('value')
        if not value or value['http'] in (401, 403, 429):
            raise RuntimeError('Portal login, access, or rate limit blocked the read-only audit')
        if value['http'] != 200:
            return {'status': 'unknown', 'approved_amount_cents': None, 'evidence': f"Status endpoint returned HTTP {value['http']}",
                    'checked_at': datetime.now(timezone.utc).isoformat()}
        body = value['body']
        if body.get('deliveryUuid') != delivery:
            raise ValueError('Portal response delivery ID does not match the requested order')
        raw = body.get('disputeStatus')
        status = {'DISPUTE_STATUS_REFUNDED': 'approved', 'DISPUTE_STATUS_DENIED': 'denied',
                  'DISPUTE_STATUS_PENDING': 'pending'}.get(raw, 'unknown')
        return {'status': status, 'raw_status': raw, 'approved_amount_cents': None,
                'evidence': 'Authenticated portal dispute-status response; monetary credits reconciled separately from the financial export',
                'checked_at': datetime.now(timezone.utc).isoformat()}

    def outcome_from_page(self, row):
        self.socket.settimeout(45)
        delivery = row['delivery_uuid']
        if not re.fullmatch(r'[a-fA-F0-9-]{36}', delivery) or not row['store_id'].isdigit():
            raise ValueError('Unexpected order identifier')
        url = f"https://www.doordash.com/merchant/financials/transactions/{delivery}?store_id={row['store_id']}"
        self.call('Page.navigate', {'url': url})
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            time.sleep(.4)
            expression = """(() => {
                const text = document.body?.innerText || '';
                if (/verify you are human|security verification|too many disputes|dispute limit/i.test(text)) return {blocked:true};
                if (/identity.doordash|merchant\\/login/.test(location.href)) return {blocked:true};
                const marker = 'Order: ' + DELIVERY.slice(-8).toUpperCase();
                if (!location.pathname.endsWith('/'+DELIVERY) || !text.includes(marker)) return null;
                const detail = text.slice(text.lastIndexOf(marker));
                const boundary = detail.indexOf('Delivery details');
                if (boundary < 0) return null;
                const heading = detail.slice(0,boundary);
                const amounts = [...heading.matchAll(/(?:receive a credit of|amount refunded|refund amount)\\s*:?\\s*\\$([\\d,]+(?:\\.\\d{2})?)/gi)].map(m=>m[1]);
                const credit = detail.match(/Error charge credit(?: \\(includes tax\\))?\\s*\\+\\$([\\d,]+(?:\\.\\d{2})?)/i);
                if (credit) amounts.push(credit[1]);
                return {heading, amounts, hasDisputeButton: [...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='Dispute charge')};
            })()""".replace('DELIVERY', json.dumps(delivery))
            result = self.call('Runtime.evaluate', {'expression': expression, 'returnByValue': True}).get('result', {}).get('value')
            if result and result.get('blocked'):
                raise RuntimeError('Portal login, verification, or cooldown blocked the audit')
            if not result:
                continue
            heading = result['heading']
            states = [status for status, pattern in [('approved', r'dispute approved'), ('denied', r'dispute denied'),
                      ('pending', r'dispute pending|under review')] if re.search(pattern, heading, re.I)]
            status = states[0] if len(states) == 1 else 'not_disputed' if not states and result['hasDisputeButton'] else 'unknown'
            from financial_export import cents
            amounts = set(cents(v.replace(',', '')) for v in result['amounts'])
            return {'status': status, 'approved_amount_cents': next(iter(amounts)) if status == 'approved' and len(amounts) == 1 else None,
                    'evidence': heading.strip(), 'checked_at': datetime.now(timezone.utc).isoformat()}
        return {'status': 'unknown', 'approved_amount_cents': None, 'evidence': 'Order detail did not load within 35 seconds',
                'checked_at': datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--port', type=int, default=9222)
    args = parser.parse_args()
    if date.fromisoformat(args.start) > date.fromisoformat(args.end):
        raise ValueError('Invalid audit period')
    rows, sources, duplicates = read_exports([args.export])
    stores = configured_stores()
    scope, orders = audit_orders(rows, args.start, args.end, stores)
    output = Path(args.output)
    run = {'version': 1, 'start': args.start, 'end': args.end, 'sources': sources, 'complete': False,
           'transaction_count': len(scope), 'expected_orders': len(orders), 'stores': stores, 'orders': {},
           'duplicate_transactions_removed': duplicates}
    if output.exists():
        prior = json.loads(output.read_text())
        if any(prior[k] != run[k] for k in ('start', 'end', 'sources', 'expected_orders')):
            raise ValueError('Cannot resume a different audit period or source')
        run['orders'] = prior['orders']
    output.parent.mkdir(parents=True, exist_ok=True)
    def checkpoint():
        temp = output.with_suffix('.tmp')
        temp.write_text(json.dumps(run, indent=2), encoding='utf-8')
        temp.replace(output)
    tab = ExistingTab(args.port)
    try:
        for row in orders:
            key = row['store_id'] + ':' + row['delivery_uuid']
            if key in run['orders']:
                continue
            run['orders'][key] = {**row, 'portal': tab.outcome(row)}
            checkpoint()
            n = len(run['orders'])
            if n % 10 == 0:
                print(f"Checked {n}/{len(orders)} orders", flush=True)
            time.sleep(.4)
        run['complete'] = len(run['orders']) == len(orders)
        run['finished_at'] = datetime.now(timezone.utc).isoformat()
        print(f"Audit collected {len(run['orders'])}/{len(orders)} order outcomes; no disputes submitted or emails sent.", flush=True)
    finally:
        checkpoint()
        tab.socket.close()


if __name__ == '__main__':
    main()
