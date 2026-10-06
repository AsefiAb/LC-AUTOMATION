"""Send one private owner notice per failed scheduled report date.

No report attachments or raw error text. A separate delivery receipt prevents
the later automatic retry slots from sending the same notice repeatedly.
"""
import json
import os
import sys
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from daily_report import DeliveryLedger, ReportError, deliver, report_date

FOLDER = Path(__file__).resolve().parent
OWNER = 'ab@caazllc.com'
REGIONS = {'CO': 'Colorado', 'AZ': 'Arizona', 'TX': 'Texas'}
CAUSES = {
    'Altametrics requested an interactive security check or account sign-in.',
    'OneDrive access, download, upload or file-version verification failed.',
    'Workbook layout, existing figures or uploaded values did not pass validation.',
    'Email delivery or its duplicate-prevention receipt was not confirmed.',
    'Altametrics source collection did not complete.',
    'Report inputs or source figures did not pass validation.',
    'The reporting process stopped before completion.'}


def names(values):
    return ', '.join(REGIONS[key] for key in REGIONS if key in values) or 'Not identified by this run'


def failure_message(day, status, run_id):
    if date.fromisoformat(day).isoformat() != day or not str(run_id).isdigit():
        raise ValueError('Invalid failure notice date or run identifier.')
    recovery = status.get('recovery', {})
    details = status.get('failure', {})
    delivered = status.get('current_delivery') in ('sent', 'already_sent')
    lines = ['The sales automation needs attention.', '', 'Report date: ' + day]
    if delivered:
        lines.append('Current daily report: delivery was confirmed; do not resend it.')
    elif status.get('current_delivery') == 'not_attempted':
        lines.append('Current daily report: this run did not attempt to send it.')
    else:
        lines.append('Current daily report: delivery is unconfirmed. Check Sent before any manual resend.')
    if recovery.get('status') == 'failed':
        lines.extend(['', 'Earlier workbook recovery remains incomplete.',
                      'Recovery regions: ' + names(recovery.get('regions', []))])
        try:
            first, last = (date.fromisoformat(recovery[key]).isoformat() for key in ('start', 'end'))
        except (KeyError, TypeError, ValueError):
            pass
        else:
            lines.append('Recovery date range: ' + first + ' through ' + last)
        if recovery.get('cause') in CAUSES:
            lines.append('Recovery issue: ' + recovery['cause'])
    if not delivered:
        failed_regions = details.get('regions', [])
        if not failed_regions and status.get('active_region') in REGIONS:
            failed_regions = [status['active_region']]
        lines.append('Affected regions: ' + names(failed_regions))
        if details.get('cause') in CAUSES:
            lines.append('Issue: ' + details['cause'])
        else:
            lines.append('Issue: the workflow stopped before a complete report could be confirmed.')
    verified = status.get('workbook_regions_verified', [])
    if verified:
        lines.append('Workbook inputs verified by this run: ' + names(verified))
    elif status.get('workbook_updates_started'):
        lines.append('Workbook updates began, but this run did not confirm all results. Review the run audit.')
    else:
        lines.append('This run has not confirmed a complete workbook update.')
    lines.extend(['', 'GitHub run: https://github.com/AsefiAb/LC-AUTOMATION/actions/runs/' + str(run_id),
                  '', 'Later scheduled retries can recover missing dates. This notice is sent once for this report date.'])
    message = EmailMessage()
    message['From'] = OWNER
    message['To'] = OWNER
    message['Subject'] = 'Sales report incomplete — ' + day
    message['Message-ID'] = '<sales-failure-' + day + '@caazllc.com>'
    message.set_content('\n'.join(lines))
    return message


def main():
    if os.environ.get('GITHUB_REPOSITORY') != 'AsefiAb/LC-AUTOMATION' or os.environ.get('GITHUB_EVENT_NAME') != 'schedule':
        raise ReportError('Failure notices are restricted to this repository scheduled runs.')
    status_file = FOLDER / 'reports' / 'run-status.json'
    status = json.loads(status_file.read_text(encoding='utf-8')) if status_file.exists() else {}
    if status.get('complete'):
        print('Report completed; no incomplete-report notice sent.')
        return 0
    config = json.loads((FOLDER / 'config.json').read_text(encoding='utf-8'))
    day = status.get('report_date') or report_date(datetime.now(timezone.utc), config['timezone'])
    message = failure_message(day, status, os.environ.get('GITHUB_RUN_ID', ''))
    result = deliver(message, DeliveryLedger(day, namespace='sales-alert'), day)
    print('Owner-only failure notice ' + result + '.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        # GitHub still shows the original job failure when SMTP/ledger also fail.
        print('Owner failure notice was not confirmed (' + type(exc).__name__ + ').', file=sys.stderr)
        sys.exit(1)
