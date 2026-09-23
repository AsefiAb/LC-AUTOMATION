"""Send a generated DoorDash tracker. SMTP password stays in the environment."""
import argparse
import json
import os
from pathlib import Path
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import make_msgid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent


def build_message(config, attachment, now, summary=None):
    if config['from'] != 'ab@caazllc.com' or set(config['to']) != {'carter@franbiznetwork.com', 'alicia@lccollc.com'}:
        raise ValueError('Email configuration differs from the requested sender/recipients')
    message = EmailMessage()
    message['From'] = config['from']
    message['To'] = ', '.join(config['to'])
    message['Subject'] = "This week's dispute savings" if summary else f"{config['subject_prefix']} — {now:%Y-%m-%d}"
    message['Message-ID'] = make_msgid(domain='caazllc.com')
    message.set_content(
        'Attached is the cumulative DoorDash dispute tracker.\n\n'
        'It separates portal-approved, denied, pending, uncertain, and unverified amounts. '
        'The Orders sheet shows when each outcome was last checked. '
        'Approval reflects the portal status; it does not confirm a bank deposit.\n'
    )
    if summary:
        money = lambda cents: f'${cents / 100:,.2f}'
        body = ('Hi Carter and Alicia,\n\n'
                f"Attached is the DoorDash dispute savings report for {summary['period_start']}–{summary['period_end']}.\n\n"
                f"{'Total historical' if summary['first_email'] else 'Newly verified'} dispute savings: {money(summary['newly_verified_savings_cents'])}\n"
                f"AB Cut: {money(summary['ab_cut_cents'])} (15%)\n")
        if summary['first_email']:
            body += f"\nNo prior AB payments have been received. Total still owed to AB: {money(summary['first_unpaid_ab_cut_cents'])}.\n"
        if summary['issues']:
            body += f"\n{len(summary['issues'])} disputes need verification and are excluded; see the Issues sheet.\n"
        body += '\nSavings reflect verified DoorDash refund approvals. Newly verified amounts may include older disputes.\n\nThanks,\nAB\n'
        message.set_content(body)
    message.add_attachment(attachment.read_bytes(), maintype='application',
                           subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                           filename=attachment.name)
    return message


def deliver(message, receipt, password, smtp_factory=smtplib.SMTP_SSL, checkpoint=lambda: None, summary=None):
    # Reserve before SMTP. A timeout or partial delivery must not trigger a duplicate.
    receipt.parent.mkdir(parents=True, exist_ok=True)
    metadata = {'status': 'sending', 'message_id': message['Message-ID']}
    if summary: metadata['ab_summary'] = {key: summary[key] for key in ('generated_at', 'period_end', 'cumulative_savings_cents', 'cumulative_ab_cut_cents')}
    with receipt.open('x', encoding='utf-8') as stream:
        json.dump(metadata, stream)
    checkpoint()  # Reserve in GitHub before SMTP; a lost runner cannot cause a resend.
    with smtp_factory('smtp.gmail.com', 465, context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(message['From'], ''.join(password.split()))
        refused = smtp.send_message(message)
        if refused:
            raise RuntimeError('One or more recipients were rejected; inspect Gmail Sent before retrying')
    temporary = receipt.with_suffix('.tmp')
    temporary.write_text(json.dumps({**metadata, 'status': 'sent'}), encoding='utf-8')
    temporary.replace(receipt)
    checkpoint()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('workbook', type=Path)
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--summary', type=Path)
    args = parser.parse_args()
    if args.workbook.suffix.lower() != '.xlsx' or not args.workbook.is_file():
        raise ValueError('Provide an existing generated XLSX tracker')
    now = datetime.now(ZoneInfo('America/Los_Angeles'))
    config = json.loads((ROOT / 'email-config.json').read_text(encoding='utf-8'))
    summary = json.loads(args.summary.read_text()) if args.summary else None
    message = build_message(config, args.workbook, now, summary)
    if args.preview:
        output = ROOT / 'reports' / 'email-preview.eml'
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(message.as_bytes())
        print(f'Preview saved: {output}; no email sent')
        return
    password = os.environ.get('CAAZ_GMAIL_APP_PASS', '')
    if not password:
        raise ValueError('CAAZ_GMAIL_APP_PASS is required in the sending environment')
    data = Path(os.environ.get('DOORDASH_DATA_DIR', ROOT / 'data'))
    # One automatic report per Pacific day, even when regenerated under a new name.
    receipt_day = summary['period_end'] if summary else f'{now:%Y-%m-%d}'
    receipt = data / 'email-receipts' / f'{receipt_day}.json'
    if summary and summary['first_email'] and summary['issues']:
        raise ValueError('Verify historical disputes before sending an all-time unpaid AB total. Preview and Issues sheet are available.')
    if receipt.exists():
        prior = json.loads(receipt.read_text())
        if prior.get('status') == 'sent':
            print('This reporting period was already sent; no duplicate email.')
            return
        raise ValueError('Prior email outcome is uncertain; check Sent before retrying.')
    checkpoint = lambda: None
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        from cloud_state import persist
        checkpoint = persist
    deliver(message, receipt, password, checkpoint=checkpoint, summary=summary)
    print('DoorDash tracker sent to Carter and Alicia.')


if __name__ == '__main__':
    main()
