"""Rebuild native ADP imports and store reports from verified source inputs."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from payroll_run import normalize, period_for, fetch_tcp
from tcp_report_fallback import apply_export
from report_package import prepare_current, verify_stores
from regional_email import delivery_ledgers, send_regions
from source_reconciliation import completion_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=['preview', 'deliver'], default='preview')
    parser.add_argument('--revision', choices=['adp-format-v2', 'adp-format-v3'])
    parser.add_argument('--refresh-sources', action='store_true')
    args = parser.parse_args()
    read = lambda name: json.loads((args.folder / name).read_text(encoding='utf-8-sig'))
    settings = read('settings.json')
    dates = settings['period']
    period_for(datetime.now(timezone.utc), dates['start_date'], dates['end_date'])
    args.output.mkdir(parents=True, exist_ok=True)
    if args.refresh_sources:
        import gateway
        source = fetch_tcp(dates)
        tips = gateway.fetch(dates)
        (args.output / 'tcp-audit.json').write_text(json.dumps(source), encoding='utf-8')
        (args.output / 'gateway-input.json').write_text(json.dumps(tips), encoding='utf-8')
    else:
        source, tips = read('tcp-audit.json'), read('gateway-input.json')
    source = apply_export(source, args.folder / 'native-payroll.xml', settings.get('approved_work_exceptions'))
    payroll = normalize(source)
    payroll['source_exceptions'] = source.get('reported_source_exceptions', [])
    dates = payroll['period']
    period_for(datetime.now(timezone.utc), dates['start_date'], dates['end_date'])
    if settings.get('period') != dates:
        raise ValueError('ADP template bundle must be confirmed for this pay period')
    messages, data, audit = prepare_current(tips, payroll, args.output, args.folder, settings)
    verify_stores(data, args.output)
    print('All 18 store workbooks verified against TCP/Gateway source calculations.')
    print(f'ADP import readiness: {audit["ready"]}; {len(audit["issues"])} unresolved checks (details in private artifacts).')
    if args.mode == 'deliver':
        if os.environ.get('GITHUB_REPOSITORY') != 'AsefiAb/LC-AUTOMATION' or os.environ.get('GITHUB_ACTIONS') != 'true':
            raise ValueError('Delivery requires the private repository workflow')
        if args.revision:
            for message in messages:
                message.replace_header('Message-ID', message['Message-ID'].replace('@', '-' + args.revision + '@'))
                # Keep the requested short summary and add one replacement notice.
                part = message.get_body(preferencelist=('plain',))
                part.set_content(part.get_content() + '\nThese files replace the reports sent earlier for this pay period.\n')
                (args.output / (message['Subject'] + '.eml')).write_bytes(message.as_bytes())
        expected = {code for code, detail in audit['regions'].items() if detail['ready'] or code in audit['issue_reports']}
        if {m['Subject'].split()[0] for m in messages} != expected:
            raise ValueError('Email requires complete payroll or a fully reconciled issues attachment')
        result = send_regions(messages, dates['end_date'], delivery_ledgers(dates['end_date'], args.revision), allow_partial=True) if messages else {}
        (args.output / 'delivery-results.json').write_text(json.dumps(result, indent=2))
    else:
        print('Preview only. No email sent and no ADP import submitted.')
    return completion_code(audit)


if __name__ == '__main__':
    raise SystemExit(main())
