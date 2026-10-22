"""Owner-confirmed TCP GEID -> ADP file number matches.

Kept in the private secret PAYROLL_ADP_IDENTITIES, never in the repository.
Shape (values below are illustrative, not a real employee):
{"version": 1, "identities": [{"geid": "1000001", "company": "FNC",
  "file_number": "099901", "tcp_name": "Pat Exampel", "adp_name": "Pat Example",
  "evidence_ref": "Owner confirmation YYYY-MM-DD"}]}
Every value is a JSON string; geid and file_number are digits only.

These resolve names that are spelled differently in TCP and ADP. A match is
used only when the confirmed file number is on the regional ADP roster under
the confirmed ADP name and the TCP name still agrees; otherwise it is reported,
never guessed.
"""
import csv
import io
import json
import os
import re

from adp_roster import name_forms

COMPANIES = {'FND': 'CO', 'FNC': 'AZ', 'ZKT': 'TX'}
REGIONS = {code: company for company, code in COMPANIES.items()}
FIELDS = {'geid', 'company', 'file_number', 'tcp_name', 'adp_name', 'evidence_ref'}
SETTINGS_REF = 'ADP_ROSTER_SETTINGS'
INVALID = 'Confirmed ADP identity list (PAYROLL_ADP_IDENTITIES) is invalid; confirmed name matches were not applied.'


def valid_entry(item):
    """True for one well-formed identity: all strings, ASCII-digit IDs, known company, names present."""
    return (isinstance(item, dict) and set(item) == FIELDS
            and all(isinstance(item[k], str) for k in FIELDS)
            and re.fullmatch(r'[0-9]+', item['geid']) is not None
            and item['company'] in COMPANIES
            and re.fullmatch(r'[0-9]+', item['file_number']) is not None
            and all(item[k].strip() for k in ('tcp_name', 'adp_name', 'evidence_ref')))


def file_key(item):
    return item['company'], item['file_number'].lstrip('0')


def load(text=None):
    """Return (identities, problems). An invalid registry is reported, not used; never raises."""
    text = os.environ.get('PAYROLL_ADP_IDENTITIES', '') if text is None else text
    try:
        if not text.strip():
            return [], []
        value = json.loads(text)
        if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('identities'), list):
            raise ValueError('shape')
        seen_geids, seen_files = set(), set()
        for item in value['identities']:
            if not valid_entry(item):
                raise ValueError('entry')
            if item['geid'] in seen_geids or file_key(item) in seen_files:
                raise ValueError('duplicate')
            seen_geids.add(item['geid'])
            seen_files.add(file_key(item))
        return value['identities'], []
    except (ValueError, TypeError, KeyError, AttributeError):
        return [], [INVALID]


def from_settings(settings):
    """Saved ADP_ROSTER_SETTINGS identities as identity dicts in the PAYROLL_ADP_IDENTITIES shape.

    settings['identities'] is {region_code: {geid: {file_number, adp_name, tcp_name}}}.
    Malformed entries are skipped; this never raises.
    """
    found = []
    try:
        saved = settings.get('identities') if isinstance(settings, dict) else None
        if not isinstance(saved, dict):
            return []
        for code in sorted(k for k in saved if isinstance(k, str)):
            company, specs = REGIONS.get(code), saved[code]
            if not company or not isinstance(specs, dict):
                continue
            for geid in sorted(k for k in specs if isinstance(k, str)):
                spec = specs[geid]
                if not isinstance(spec, dict):
                    continue
                item = {'geid': geid, 'company': company, 'file_number': spec.get('file_number'),
                        'tcp_name': spec.get('tcp_name'), 'adp_name': spec.get('adp_name'),
                        'evidence_ref': SETTINGS_REF}
                if valid_entry(item):
                    found.append(item)
    except Exception:
        return found
    return found


def merged(settings):
    """(identities, problems): PAYROLL_ADP_IDENTITIES first, then saved settings matches that do not conflict.

    A saved match is skipped when its GEID or its (company, file number) is
    already used; an exact repeat of an accepted match is skipped silently.
    Never raises.
    """
    identities, problems = load()
    identities, problems = list(identities), list(problems)
    geids = {item['geid']: item for item in identities}
    files = {file_key(item) for item in identities}
    conflicts = 0
    for item in from_settings(settings):
        same = geids.get(item['geid'])
        if same is not None and file_key(same) == file_key(item):
            continue
        if same is not None or file_key(item) in files:
            conflicts += 1
            continue
        identities.append(item)
        geids[item['geid']] = item
        files.add(file_key(item))
    if conflicts:
        problems.append(f'{conflicts} saved ADP_ROSTER_SETTINGS identity match(es) conflict with another confirmed '
                        'match for the same GEID or ADP file and were not used.')
    return identities, problems


def by_geid(identities):
    return {item['geid']: item for item in identities}


def overrides_for(raw, company, code, identities=None):
    """Return (overrides, notes) in fill_region's identity format for one regional template.

    A confirmed match applies only when its file number is on the roster and the
    roster row still carries the confirmed ADP name (case and spacing aside, or an
    alternate name ADP writes in parentheses in that row), so a mistyped or
    reassigned file number never receives someone else's pay.
    """
    if identities is None:
        identities, problems = load()
    else:
        problems = []
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp1252')
    rows = {r[2].lstrip('0'): r for r in csv.reader(io.StringIO(text)) if len(r) == 9 and r[0] == company}
    overrides, notes = {}, list(problems)
    for item in identities:
        if item['company'] != company:
            continue
        row = rows.get(item['file_number'].lstrip('0'))
        if row is None:
            notes.append(f'{code} GEID {item["geid"]}: confirmed ADP file {item["file_number"]} is not on the ADP roster '
                         'for this region (not on the last ADP export or the live ADP roster); match not applied.')
            continue
        if not name_forms(item['adp_name']) & name_forms(row[4]):
            notes.append(f'{code} GEID {item["geid"]}: confirmed ADP file {item["file_number"]} no longer has the '
                         'confirmed ADP name on the roster; match not applied.')
            continue
        overrides[item['geid']] = {'file_number': row[2], 'adp_name': row[4], 'tcp_name': item['tcp_name']}
    return overrides, notes
