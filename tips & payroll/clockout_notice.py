"""Manually requested email about current TCP missing clock-outs; no payroll edits."""
import argparse
import json
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from payroll_run import fetch_tcp, period_for
from payroll_email_summary import date_label
from summarize_inspection import summarize, store_label


def missing_clockouts(report):
    summary = summarize(report)  # Requires complete TCP sources with no API errors.
    rows = {s['segment_id']: {**s, 'detail': 'Clock-out missing in TCP.'}
            for s in summary['segments'] if s['category'] == 'Open punch'}
    calculated = {s['segment_id']: s for s in summary['segments']}
    data = report['datasets']
    people = {str(p['employeeId']): p for p in data['employees']['records']}
    jobs = {str(j['jobCodeId']): j for j in data['jobcodes']['records']}
    first, last = report['period']['startDate'], report['period']['stopDate']
    for raw in data['worksegments']['records']:
        if raw.get('timeOut') or not first <= (raw.get('timeIn') or '')[:10] <= last:
            continue
        sid, eid = str(raw['id']), str(raw['employeeId'])
        if sid in rows:
            continue
        person = people.get(eid, {})
        rows[sid] = {'segment_id': sid, 'employee_id': eid,
            'name': person.get('employeeName') or ' '.join(filter(None, [person.get('firstName'), person.get('lastName')])) or 'Employee ' + eid,
            'store': store_label(jobs.get(str(raw.get('jobCodeId')), {}).get('locationName')),
            'time_in': raw['timeIn'],
            'detail': ('Clock-out differs between TCP records; verify the timecard.' if sid in calculated
                       else 'Clock-out missing in TCP.')}
    return sorted(rows.values(), key=lambda r: (r['store'], r['name'], r['time_in'], r['segment_id']))


def build_message(report):
    rows = missing_clockouts(report)
    dates = {'start_date': report['period']['startDate'], 'end_date': report['period']['stopDate']}
    label = date_label(dates)
    checked = datetime.fromisoformat(report['generated_at']).astimezone(ZoneInfo('America/Los_Angeles'))
    message = EmailMessage()
    message['From'] = 'ab@caazllc.com'
    message['To'] = 'alicia@lccollc.com'
    message['Subject'] = 'Missing TCP clock-outs - ' + label
    message['Message-ID'] = f'<payroll-clockouts-{dates["start_date"]}-{dates["end_date"]}@caazllc.com>'
    lines = ['Hi Alicia,', '', f'I checked TCP for payroll {label}.', '']
    if rows:
        lines.append('These timecards need their clock-outs checked:')
        lines.append('')
        for row in rows:
            when = datetime.fromisoformat(row['time_in']).strftime('%B %d at %I:%M %p')
            lines.append(f'- {row["name"]} (ID {row["employee_id"]}), store {row["store"]}: clocked in {when}. {row["detail"]}')
        lines.extend(['', 'Please have the store managers confirm the actual departure times and correct these timecards in TCP. Let me know once corrected so we can finish the affected hours, overtime and tips.'])
    else:
        lines.append('TCP currently shows no missing clock-outs for this period.')
    lines.extend(['', 'Clock-in times are as shown in TCP.',
                  'Checked ' + checked.strftime('%B %d, %Y at %I:%M %p %Z') + '.', '', 'Thanks,', 'AB'])
    message.set_content('\n'.join(lines))
    return message, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    args = parser.parse_args()
    dates = period_for(datetime.now(timezone.utc), args.start, args.end)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sales automation'))
    from daily_report import DeliveryLedger, deliver
    ledger = DeliveryLedger(dates['end_date'] + '-clockout-notice', namespace='payroll')
    previous = ledger.read()
    if previous:
        if previous.get('status') == 'sent':
            print('Missing-clock-out email already sent; not repeated.')
            return
        raise ValueError('Prior email outcome is uncertain; check Sent before retrying.')
    report = fetch_tcp(dates)
    message, rows = build_message(report)
    output = Path(__file__).parent / 'reports' / 'clockout-notice' / (args.start + '_' + args.end)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'tcp-audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (output / 'notice.eml').write_bytes(message.as_bytes())
    status = deliver(message, ledger, dates['end_date'])
    (output / 'delivery.json').write_text(json.dumps({'status': status, 'count': len(rows),
        'from': message['From'], 'to': message['To'], 'subject': message['Subject'],
        'message_id': message['Message-ID'], 'period': dates}, indent=2), encoding='utf-8')
    print('Missing-clock-out email: ' + status)


if __name__ == '__main__':
    main()
