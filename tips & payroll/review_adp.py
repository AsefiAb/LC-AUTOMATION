"""Native EPI candidates delivered even when source payroll needs correction.

Keep final-export validation strict. These separate candidates preserve the
last supplied ADP identities/control rows and leave unknown amounts blank.
"""
import base64
import json
import os

from adp_roster import FILES, fill_region, validate_import


def current_template(code, company, period_start=None):
    """The EPI file to fill: built from the live ADP roster, else the last supplied export.

    Returns (raw, notes, added, live). live is True when every current ADP employee
    came straight from ADP this run, which makes the roster current for the period;
    added lists the people who were not in the last supplied export. A saved export
    that cannot be decoded raises ValueError.
    """
    import adp_api
    value = os.environ.get('ADP_' + code + '_TEMPLATE', '')
    saved = base64.b64decode(value, validate=True) if value else None
    workers = None if adp_api.missing_secrets() else adp_api.workers_once()[0]
    if workers:
        try:
            raw, rows = adp_api.build_epi(company, workers, period_start)
        except Exception:
            rows = []
        if rows:
            added = rows
            if saved is not None:
                import csv, io
                text = saved.decode('utf-8-sig', 'replace')
                known = {r[2].lstrip('0') for r in csv.reader(io.StringIO(text)) if len(r) == 9 and r[0] == company}
                added = [r for r in rows if r['file_number'] not in known]
            notes = [f'{code}: CSV built from the live ADP roster ({len(rows)} current employees)'
                     + (f', including {len(added)} not in the last supplied export.' if saved is not None and added else '.')]
            return raw, notes, added, True
    if saved is None:
        return None, [], [], False
    raw, notes, added = live_roster(saved, company, code)
    return raw, notes, added, False


def live_roster(raw, company, code):
    """Add active ADP workers missing from the last export; returns (raw, notes, added)."""
    import adp_api
    if adp_api.missing_secrets():
        return raw, [], []
    workers, problem = adp_api.workers_once()
    if workers is None:
        return raw, [f'{code}: live ADP roster unavailable ({problem}); CSV uses the last supplied export only.'], []
    try:
        merged, added = adp_api.augment_template(raw, company, workers)
    except adp_api.ADPError as exc:
        return raw, [f'{code}: live ADP roster could not be merged ({exc}).'], []
    except Exception as exc:  # Optional step: an unexpected roster shape never stops payroll.
        return raw, [f'{code}: live ADP roster could not be merged ({type(exc).__name__}).'], []
    raw = merged
    notes = [f'{code}: live ADP roster added {len(added)} active worker(s) missing from the last export, '
             'using their ADP file number, legal name and hourly rate.'] if added else []
    return raw, notes, added


# fill_region rejects this spec (no such ADP row), so the person is reported as
# missing with an issue instead of being exact-name matched to a stale row.
BLOCKED = {'file_number': '', 'adp_name': '', 'tcp_name': ''}


def saved_overrides(settings, code):
    """Saved roster-settings matches for one region ({} when the settings are malformed)."""
    saved = settings.get('identities') if isinstance(settings, dict) else None
    region = saved.get(code) if isinstance(saved, dict) else None
    return dict(region) if isinstance(region, dict) else {}


def identity_overrides(raw, company, code, settings):
    """Saved roster-settings matches plus owner-confirmed TCP/ADP name matches.

    Confirmed matches (PAYROLL_ADP_IDENTITIES) are resolved against the final
    regional roster, so a match to a newly added ADP worker also applies. Every
    match, saved or confirmed, is then checked against live ADP status when the
    roster can be read: a current assignment is kept; an older assignment of the
    same ADP person moves to that person's newest file only when that row is on
    this regional roster; anything else (terminated, missing, replacement not on
    the roster) is blocked so the person is reported, never matched by name.
    Optional step: any failure returns the saved matches with a note; never raises.
    """
    try:
        return checked_overrides(raw, company, code, settings)
    except Exception as exc:
        try:
            saved = saved_overrides(settings, code)
        except Exception:
            saved = {}
        return saved, [f'{code}: confirmed ADP identity list could not be applied ({type(exc).__name__}); '
                       'confirmed name matches were not used.']


def checked_overrides(raw, company, code, settings):
    import csv
    import io
    import adp_api
    from adp_identities import overrides_for
    overrides = saved_overrides(settings, code)
    confirmed, notes = overrides_for(raw, company, code)
    overrides.update(confirmed)
    if not overrides or adp_api.missing_secrets():
        return overrides, notes
    workers, problem = adp_api.workers_once()  # Cached: the same read live_roster used.
    if workers is None:
        notes.append(f'{code}: matched ADP files could not be checked against live ADP status ({problem}).')
        return overrides, notes
    current, older = adp_api.current_workers(workers, company)
    if not current:
        notes.append(f'{code}: live ADP roster lists no active {company} workers; '
                     'matched ADP files could not be checked against live ADP status.')
        return overrides, notes
    live = {w['file_number'].lstrip('0') for w in current}
    replaced = {w['file_number'].lstrip('0'): w['replaced_by'] for w in older}
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp1252')
    roster = {r[2].lstrip('0'): r for r in csv.reader(io.StringIO(text)) if len(r) == 9 and r[0] == company}
    for geid in sorted(overrides, key=str):
        spec = overrides[geid]
        shown = spec.get('file_number') if isinstance(spec, dict) else None
        number = str(shown or '').lstrip('0')
        if not number:
            notes.append(f'{code} GEID {geid}: saved ADP identity has no usable ADP file number; match not applied.')
            overrides[geid] = dict(BLOCKED)
            continue
        if number in live:
            continue
        if number in replaced:
            newest = replaced[number]
            row = roster.get(newest.lstrip('0'))
            if row is not None and isinstance(spec.get('tcp_name'), str):
                notes.append(f'{code} GEID {geid}: matched ADP file {shown} is an older assignment of the same ADP '
                             f'person; using the newest ADP file {row[2]}.')
                overrides[geid] = {'file_number': row[2], 'adp_name': row[4], 'tcp_name': spec['tcp_name']}
                continue
            reason = f'was replaced by newer ADP file {newest}, which is not on this region\'s ADP roster'
        else:
            reason = 'is terminated or not active in ADP'
        notes.append(f'{code} GEID {geid}: matched ADP file {shown} {reason}; match not applied.')
        overrides[geid] = dict(BLOCKED)
    return overrides, notes


def prepare_candidate(prefix, people, dates, output, rates=None):
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
    try:
        raw, roster_notes, added, live = current_template(code, company, dates['start_date'])
    except (ValueError, TypeError):
        return None, {'ready': False, 'issues': [f'{code}: ADP template/identity validation failed; CSV could not be created']}
    if raw is None:
        return None, {'ready': False, 'issues': roster_notes + [f'{code}: missing ADP template; CSV could not be created']}
    if not live and settings.get('period') != dates:
        notes.append(f'{code}: CSV uses the last supplied ADP roster and batch controls. '
                     'Confirm current employees, file numbers, and batch in ADP; current-period roster is not verified.')
    try:
        notes.extend(roster_notes)
        overrides, identity_notes = identity_overrides(raw, company, code, settings)
        notes.extend(identity_notes)
        content, audit = fill_region(None, prefix, raw, overrides,
                                     settings.get('allow_missing_zero', False), review_people=people)
        from adp_roster import apply_tcp_rates
        content = apply_tcp_rates(content, company, audit, rates)
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
