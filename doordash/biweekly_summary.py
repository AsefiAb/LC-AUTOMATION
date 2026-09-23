"""AB earns 15% of verified refunds; first report all time, then new recoveries."""
import argparse
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo('America/Los_Angeles')
ANCHOR = date(2026, 9, 22)


def cut(cents):
    return (cents * 15 + 50) // 100


def period(now):
    today = now.astimezone(PACIFIC).date()
    due = ANCHOR + timedelta(days=((today - ANCHOR).days // 14) * 14)
    return due - timedelta(days=14), due - timedelta(days=1)


def schedule_due(now):
    local = now.astimezone(PACIFIC)
    return local.date() >= ANCHOR and (local.date() - ANCHOR).days % 14 == 0 and local.hour >= 8


def summarize(ledger, receipts, now, tracker_snapshot=None):
    start, end = period(now)
    if any(r.get('status') == 'sending' and 'ab_summary' in r for r in receipts.values()):
        raise ValueError('An earlier savings email has an uncertain outcome; reconcile it before calculating the next AB share.')
    eligible, issues = [], []
    if tracker_snapshot:
        for name in tracker_snapshot['platforms']:
            if name != 'DoorDash':
                issues.append({'key': 'platform:' + name, 'store': name,
                    'reason': f'Only the historical tracker snapshot ({tracker_snapshot["generated_at"]}) is available. Current dated refunds are required; this platform must not be silently omitted or billed again.'})
    for key, row in ledger['orders'].items():
        if row['status'] in ('observed', 'uncertain', 'submitting'):
            issues.append({'key': key, 'store': row['store'], 'reason': 'Historical outcome needs verification.'})
        if row['status'] != 'approved': continue
        amount = row.get('approvedAmountCents')
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            issues.append({'key': key, 'store': row['store'], 'reason': 'Approved refund amount is unverified; original charge is not used as savings.'})
            continue
        eligible.append({'key': key, 'store': row['store'], 'approved_cents': amount,
                         'verified_at': row.get('lastCheckedAt', '')})
    sent = [r for r in receipts.values() if r.get('status') == 'sent' and 'ab_summary' in r]
    sent.sort(key=lambda r: r['ab_summary']['generated_at'])
    previous = sent[-1]['ab_summary'] if sent else None
    total = sum(r['approved_cents'] for r in eligible)
    earned = cut(total)
    prior_total = previous['cumulative_savings_cents'] if previous else 0
    prior_cut = previous['cumulative_ab_cut_cents'] if previous else 0
    return {'version': 1, 'generated_at': now.isoformat(), 'period_start': str(start),
            'period_end': str(end), 'first_email': previous is None,
            'cumulative_savings_cents': total, 'cumulative_ab_cut_cents': earned,
            'newly_verified_savings_cents': total - prior_total, 'ab_cut_cents': earned - prior_cut,
            'first_unpaid_ab_cut_cents': earned if previous is None else None,
            'orders': eligible, 'issues': issues,
            'basis': 'Verified portal-approved refunds, not bank-deposit confirmation. First report includes all tracked history; subsequent reports include changes since the previous sent report.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--schedule-gate', action='store_true')
    args = parser.parse_args()
    now = datetime.now(PACIFIC)
    if args.schedule_gate:
        print('true' if schedule_due(now) else 'false')
        return
    data = Path(os.environ['DOORDASH_DATA_DIR'])
    snapshot_file = data / 'tracker-snapshot.json'
    summary = summarize(json.loads((data / 'ledger.json').read_text()),
        {p.stem: json.loads(p.read_text()) for p in (data / 'email-receipts').glob('*.json')}, now,
        json.loads(snapshot_file.read_text()) if snapshot_file.exists() else None)
    out = Path(__file__).parent / 'reports' / 'biweekly-summary.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print('Biweekly savings and 15% AB share calculated; unverified amounts are separate.')


if __name__ == '__main__': main()
