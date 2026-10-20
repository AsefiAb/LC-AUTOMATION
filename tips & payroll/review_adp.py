"""Native EPI candidates delivered even when source payroll needs correction.

Keep final-export validation strict. These separate candidates preserve the
last supplied ADP identities/control rows and leave unknown amounts blank.
"""
import base64
import json
import os

from adp_roster import FILES, fill_region, validate_import


def live_roster(raw, company, code):
    """Add active ADP workers missing from the last export; returns (raw, notes, added)."""
    import adp_api
    if adp_api.missing_secrets():
        return raw, [], []
    workers, problem = adp_api.workers_once()
    if workers is None:
        return raw, [f'{code}: live ADP roster unavailable ({problem}); CSV uses the last supplied export only.'], []
    try:
        raw, added = adp_api.augment_template(raw, company, workers)
    except adp_api.ADPError as exc:
        return raw, [f'{code}: live ADP roster could not be merged ({exc}).'], []
    notes = [f'{code}: live ADP roster added {len(added)} active worker(s) missing from the last export, '
             'using their ADP file number, legal name and hourly rate.'] if added else []
    return raw, notes, added


def prepare_candidate(prefix, people, dates, output):
    code, company, filename = FILES[prefix]
    notes = []
    value = os.environ.get('ADP_' + code + '_TEMPLATE', '')
    try:
        settings = json.loads(os.environ.get('ADP_ROSTER_SETTINGS') or '{}')
        if not isinstance(settings, dict):
            raise ValueError('Invalid settings')
    except (ValueError, TypeError):
        # Without the saved identity mappings, even an exact name may refer to
        # a different person. Fail this candidate, while delivering the reports.
        return None, {'ready': False, 'issues': [f'{code}: invalid ADP identity settings; CSV could not be created']}
    if settings.get('period') != dates:
        notes.append(f'{code}: CSV uses the last supplied ADP roster and batch controls. '
                     'Confirm current employees, file numbers, and batch in ADP; current-period roster is not verified.')
    if not value:
        return None, {'ready': False, 'issues': [f'{code}: missing ADP template; CSV could not be created']}
    try:
        raw = base64.b64decode(value, validate=True)
        raw, roster_notes, added = live_roster(raw, company, code)
        notes.extend(roster_notes)
        content, audit = fill_region(None, prefix, raw, settings.get('identities', {}).get(code),
                                     settings.get('allow_missing_zero', False), review_people=people)
        audit['import_format'] = validate_import(prefix, filename, content, allow_blank=True)
    except (ValueError, KeyError, TypeError):
        return None, {'ready': False, 'issues': [f'{code}: ADP template/identity validation failed; CSV could not be created']}
    audit['issues'] = notes + audit['issues']
    audit['adp_added'] = [{k: r[k] for k in ('file_number', 'name', 'rate', 'hire_date')} for r in added]
    audit['period'] = dates
    audit['template_period'] = settings.get('period')
    audit['incomplete'] = True
    (output / filename).write_bytes(content)
    (output / f'{code}-epi-candidate-audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    return filename, audit


def issue_notes(audit):
    notes = audit['issues'] + audit.get('warnings', [])
    for p in audit.get('missing', []):
        amounts = ', '.join(f'{k.replace("_", " ")}: {p[k] if p[k] is not None else "unverified"}'
                           for k in ('regular_hours', 'overtime_hours', 'tips'))
        notes.append(f'NOT IN CSV: {p["name"]} (GEID {p["geid"]}); {amounts}. '
                     f'{p["reason"]}. Confirm ADP file number or review for addition/termination; no identity invented.')
    if audit.get('totals'):
        notes.append('CSV known matched amounts: ' + ', '.join(f'{k}: {v}' for k, v in audit['totals'].items()))
    return notes
