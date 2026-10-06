"""Read-only, one-store Gateway Financial View evidence. No emails or updates."""
import argparse
import asyncio
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gateway


def validate_request(store, start, end, today=None):
    selection = gateway.selected_stores([store])
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    today = today or datetime.now(ZoneInfo('America/Los_Angeles')).date()
    if not 0 <= (last - first).days <= 30 or last >= today or (today - first).days > 366:
        raise ValueError('Use a completed date range of 1–31 days within the past 366 days')
    return {'start_date': first.isoformat(), 'end_date': last.isoformat()}, next(iter(selection))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    args = parser.parse_args()
    dates, region = validate_request(args.store, args.start, args.end)
    output = Path(__file__).parent / 'reports' / 'gateway-financial-inspect' / args.store / (args.start + '_' + args.end)
    output.mkdir(parents=True, exist_ok=True)
    request = {'store': args.store, 'region': region, 'period': dates,
               'purpose': 'Read-only Gateway evidence; no emails, TCP requests, or workbook updates'}
    (output / 'request.json').write_text(json.dumps(request, indent=2), encoding='utf-8')
    result = asyncio.run(gateway.collect(dates, stores=[args.store], capture_financial=True, diagnostic_folder=output))
    (output / 'capture.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    verified = len(result['stores']) == 1 and result['stores'][0]['source_verified']
    print(json.dumps({'store': args.store, 'period': dates, 'rendered_source_verified': verified}))
    print('Private artifact contains every visible Financial View row/header; no sales or closed-store conclusion was inferred.')
    return 0 if verified else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, gateway.GatewayError, RuntimeError) as exc:
        print(str(exc))
        raise SystemExit(1)
