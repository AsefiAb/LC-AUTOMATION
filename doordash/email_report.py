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


def build_message(config, attachment, now):
    if config['from'] != 'ab@caazllc.com' or set(config['to']) != {'carter@franbiznetwork.com', 'alicia@lccollc.com'}:
        raise ValueError('Email configuration differs from the requested sender/recipients')
    message = EmailMessage()
    message['From'] = config['from']
    message['To'] = ', '.join(config['to'])
    message['Subject'] = f"{config['subject_prefix']} — {now:%Y-%m-%d}"
    message['Message-ID'] = make_msgid(domain='caazllc.com')
    message.set_content(
        'Attached is the cumulative DoorDash dispute tracker.\n\n'
        'It separates portal-approved, denied, pending, uncertain, and unverified amounts. '
        'The Orders sheet shows when each outcome was last checked. '
        'Approval reflects the portal status; it does not confirm a bank deposit.\n'
    )
    message.add_attachment(attachment.read_bytes(), maintype='application',
                           subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                           filename=attachment.name)
    return message


def deliver(message, receipt, password, smtp_factory=smtplib.SMTP_SSL):
    # Reserve before SMTP. A timeout or partial delivery must not trigger a duplicate.
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with receipt.open('x', encoding='utf-8') as stream:
        json.dump({'status': 'sending', 'message_id': message['Message-ID']}, stream)
    with smtp_factory('smtp.gmail.com', 465, context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(message['From'], ''.join(password.split()))
        refused = smtp.send_message(message)
        if refused:
            raise RuntimeError('One or more recipients were rejected; inspect Gmail Sent before retrying')
    temporary = receipt.with_suffix('.tmp')
    temporary.write_text(json.dumps({'status': 'sent', 'message_id': message['Message-ID']}), encoding='utf-8')
    temporary.replace(receipt)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('workbook', type=Path)
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    if args.workbook.suffix.lower() != '.xlsx' or not args.workbook.is_file():
        raise ValueError('Provide an existing generated XLSX tracker')
    now = datetime.now(ZoneInfo('America/Los_Angeles'))
    config = json.loads((ROOT / 'email-config.json').read_text(encoding='utf-8'))
    message = build_message(config, args.workbook, now)
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
    receipt = data / 'email-receipts' / f'{now:%Y-%m-%d}.json'
    deliver(message, receipt, password)
    print('DoorDash tracker sent to Carter and Alicia.')


if __name__ == '__main__':
    main()
