"""Prepare and deliver one date-labelled payroll email per region."""
import hashlib
import sys
from email.message import EmailMessage
from pathlib import Path

from allocate_tips import cents, decimal_text
from payroll_email_summary import date_label, regional_people

REGIONS = {'3910': ('TX', 'Texas'), '3759': ('AZ', 'Arizona'), '3356': ('CO', 'Colorado')}


def regional_filename(region, dates):
    return f'{REGIONS[region][0]} TIPS {date_label(dates)}.xlsx'


def employee_lines(people):
    return '\n'.join(f'{p["first_name"]} {p["last_name"]} ({p["geid"]}) — {p["job_title"]}: '
                     f'Reg {p["regular_hours"]} | OT {p["overtime_hours"]} | Tips ${p["tips"]}' for p in people) or 'None.'


def build_region_messages(summary, sources):
    label = date_label(summary['period'])
    grouped = regional_people(summary)
    files = {Path(s['path']).name: s for s in sources}
    if len(files) != len(sources):
        raise ValueError('Duplicate payroll attachment filename')
    messages = []
    for region, (code, name) in REGIONS.items():
        people = grouped[region]
        totals = {key: decimal_text(sum(cents(p[key], key) for p in people))
                  for key in ('regular_hours', 'overtime_hours', 'total_hours', 'tips')}
        expected = [regional_filename(region, summary['period'])] + [
            f'{sid} {label}.xlsx' for sid in summary['stores'] if sid.startswith(region + '-')]
        if any(filename not in files for filename in expected):
            raise ValueError(f'Missing {code} payroll attachments')
        message = EmailMessage()
        message['From'] = 'ab@caazllc.com'
        message['To'] = 'alicia@lccollc.com'
        message['Subject'] = f'{code} TIPS {label}'
        message['Message-ID'] = f'<payroll-{code}-{summary["period"]["start_date"]}-{summary["period"]["end_date"]}@caazllc.com>'
        body = (f'Hi Alicia,\n\nAttached are the {name} tips and payroll reports for {label}.\n\n'
                f'Regular hours: {totals["regular_hours"]}\nOvertime hours: {totals["overtime_hours"]}\n'
                f'Total hours: {totals["total_hours"]}\nTotal tips: ${totals["tips"]}\n\n'
                'The regional totals file has one worksheet listing each person once. Individual store files are also attached. '
                'Hours are decimals. Tips stay within the store where they were earned and go only to crew and crew leaders. '
                'Managers, co-managers and assistant managers receive no tips.\n\n'
                'Managers — combined within this region\n' + employee_lines([p for p in people if p['management']]) + '\n\n'
                'All employees and managers\n' + employee_lines(people) + '\n\n'
                'Managers listed above are also in the full employee list. Count each person once.\n')
        if summary.get('warnings'):
            body += '\nRoster setup notes: ' + '. '.join(summary['warnings']) + '.\n'
        message.set_content(body + '\nThanks,\nAB\n')
        for filename in expected:
            spec = files[filename]
            content = Path(spec['path']).read_bytes()
            if hashlib.sha256(content).hexdigest() != spec['sha256']:
                raise ValueError('Payroll attachment changed after verification')
            message.add_attachment(content, maintype='application',
                subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename=filename)
        if len(message.as_bytes()) > 24_000_000:
            raise ValueError('Regional payroll email exceeds message size limit')
        messages.append(message)
    return messages


def delivery_ledgers(end_date):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'sales automation'))
    from daily_report import DeliveryLedger
    legacy = DeliveryLedger(end_date, namespace='payroll').read()
    if legacy:
        raise ValueError('Legacy combined payroll delivery exists; review before sending regional emails')
    ledgers = {code: DeliveryLedger(end_date + '-' + code, namespace='payroll') for code, _ in REGIONS.values()}
    # Inspect all regions before any send; an uncertain prior SMTP outcome must
    # be resolved first. Confirmed sends are skipped on a retry.
    for ledger in ledgers.values():
        previous = ledger.read()
        if previous and previous.get('status') != 'sent':
            raise ValueError('An earlier regional email has an uncertain outcome; check Gmail Sent')
    return ledgers


def send_regions(messages, end_date, ledgers=None):
    ledgers = ledgers or delivery_ledgers(end_date)
    from daily_report import deliver
    if len(messages) != 3 or [m['Subject'].split()[0] for m in messages] != ['TX', 'AZ', 'CO']:
        raise ValueError('Exactly one TX, AZ and CO email is required')
    results = {}
    for message in messages:
        code = message['Subject'].split()[0]
        results[code] = deliver(message, ledgers[code], end_date)
        print(f'{code} payroll email: {results[code]}', flush=True)
    return results
