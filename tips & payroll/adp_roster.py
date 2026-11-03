"""Match current ADP rosters without inventing identities or earnings codes."""
import csv
import hashlib
import io
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from adp_export import read_template
from allocate_tips import cents, decimal_text
from payroll_email_summary import regional_people

FILES = {'3356': ('CO', 'FND', 'EPIFND00.CSV'),
         '3759': ('AZ', 'FNC', 'EPIFNC00.CSV'),
         '3910': ('TX', 'ZKT', 'EPIZKT00.CSV')}


def name_key(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def name_forms(value):
    """name_key of a roster name plus the names ADP itself writes into it.

    An EPI name such as 'Pat Example(Patty Example)' also stands for 'Pat Example'
    (the name without its parenthesised part) and 'Patty Example' (the alternate
    name ADP supplies, which fill_region also matches). Never a fuzzy or guessed form.
    """
    value = str(value or '')
    forms = {name_key(value), name_key(re.sub(r'\([^()]*\)', ' ', value))}
    forms.update(name_key(alias) for alias in re.findall(r'\(([^()]+)\)', value))
    return {form for form in forms if form}


def validate_import(region, filename, raw, *, allow_blank=False):
    """Require a native ADP CSV, never an Excel workbook with a renamed suffix."""
    code, company, expected = FILES[region]
    if filename != expected:
        raise ValueError(f'{code}: ADP upload must be named {expected}')
    if raw.startswith((b'PK', b'\xd0\xcf\x11\xe0')) or b'\x00' in raw:
        raise ValueError(f'{code}: ADP upload must contain CSV text, not an Excel workbook')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp1252')
    rows = [r for r in csv.reader(io.StringIO(text)) if r and r[0] == company and len(r) == 9]
    batches = {r[1] for r in rows}
    if len(batches) != 1:
        raise ValueError(f'{code}: CSV must contain one {company} payroll batch')
    parsed = read_template(raw, company, next(iter(batches)))
    for row in parsed['employees'].values():
        if any(not (allow_blank and value == '') and not re.fullmatch(r'\d+\.\d{2}', value) for value in row[6:]):
            raise ValueError(f'{code}: hours and tips must be nonnegative decimal values with two places')
    return {'filename': expected, 'company_code': company,
            'employee_rows': len(parsed['employees']), 'content_type': 'text/csv'}


def fill_region(summary, region, raw, overrides=None, allow_missing_zero=False, *, review_people=None):
    code, company, filename = FILES[region]
    # Identify the batch from employee rows; reject multiple/wrong batches.
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp1252')
    rows = [r for r in csv.reader(io.StringIO(text)) if r and r[0] and not r[0].startswith('!')]
    batches = {r[1] for r in rows if len(r) == 9 and r[0] == company}
    if len(batches) != 1:
        raise ValueError(f'{code}: expected one ADP batch for {company}')
    template = read_template(raw, company, next(iter(batches)))
    review = review_people is not None
    people = review_people if review else regional_people(summary)[region]
    # Last name, then first name, so exception lists and notes read alphabetically.
    people = sorted(people, key=lambda p: (str(p.get('last_name', '')).casefold(),
                                           str(p.get('first_name', '')).casefold(), str(p.get('geid', ''))))
    def amounts(person):
        if person is None:
            return [''] * 3 if review else ['0.00'] * 3
        return [('' if person[k] is None else person[k]) if review else person[k]
                for k in ('regular_hours', 'overtime_hours', 'tips')]
    names = defaultdict(list)
    for target, row in template['employees'].items():
        names[name_key(row[4])].append(target)
        # ADP itself explicitly supplies the alternate name in parentheses.
        # This is not fuzzy matching or a guessed nickname.
        for alias in re.findall(r'\(([^()]+)\)', row[4]):
            if target not in names[name_key(alias)]:
                names[name_key(alias)].append(target)
    mappings, used, issues, warnings, missing = {}, set(), [], [], []
    if review:
        issues.append(f'{code}: incomplete payroll candidate; reconcile source issues and current ADP roster before finalizing')
    overrides = overrides or {}
    for person in people:
        geid = person['geid']
        fullname = person['first_name'] + ' ' + person['last_name']
        spec = overrides.get(geid)
        if spec:
            target = (company, spec['file_number'])
            if (target not in template['employees'] or spec['adp_name'] != template['employees'][target][4]
                    or name_key(spec['tcp_name']) != name_key(fullname)):
                issues.append(f'{code} GEID {geid}: saved identity no longer agrees with both rosters')
                missing.append(dict(region=code, geid=geid, name=fullname,
                    **{k: person[k] for k in ('regular_hours', 'overtime_hours', 'tips')},
                    reason='Saved ADP identity no longer agrees with both rosters'))
                continue
            matches = [target]
        else:
            matches = names.get(name_key(fullname), [])
        if len(matches) != 1 or matches[0] in used:
            zero = all(person[k] is not None and cents(person[k], k) == 0
                       for k in ('regular_hours', 'overtime_hours', 'tips'))
            detail = dict(region=code, geid=geid, name=fullname, regular_hours=person['regular_hours'],
                          overtime_hours=person['overtime_hours'], tips=person['tips'],
                          reason='ADP identity missing or ambiguous')
            missing.append(detail)
            if zero and not matches and allow_missing_zero:
                warnings.append(f'{code} GEID {geid}: zero-hour person retained in exception list; no ADP number invented')
            else:
                issues.append(f'{code} GEID {geid}: ADP identity missing or ambiguous')
            continue
        target = matches[0]
        used.add(target)
        mappings[target] = person
    # Preserve native ADP employee order: the supplied export is already sorted
    # by ADP last name, including multiword surnames and legal/preferred names.
    output, audit_rows = [], []
    for target, row, original in template['parts']:
        if target is None:
            output.append(original)
            continue
        person = mappings.get(target)
        values = amounts(person)
        if not person:
            warnings.append(f'{code} ADP file {row[2]} ({row[4]}): no matched TCP work in this region; '
                            + ('amounts blank pending roster review' if review else 'retained at zero'))
        elif review and '' in values:
            issues.append(f'{code} GEID {person["geid"]} ({row[4]}): unverified '
                          + ', '.join(k for k, v in zip(('regular hours', 'overtime hours', 'tips'), values) if v == '')
                          + '; left blank in CSV, not zero')
        buffer = io.StringIO(newline='')
        csv.writer(buffer, lineterminator=template['newline']).writerow(row[:6] + values)
        output.append(buffer.getvalue())
        audit_rows.append({'file_number': row[2], 'adp_name': row[4], 'geid': person['geid'] if person else '',
                           'regular_hours': values[0], 'overtime_hours': values[1], 'tips': values[2]})
    content = ''.join(output).encode(template['encoding'])
    saved = read_template(content, company, next(iter(batches)))
    if [raw for k, _, raw in saved['parts'] if k is None] != [raw for k, _, raw in template['parts'] if k is None]:
        raise ValueError('ADP control rows changed')
    if list(saved['employees']) != list(template['employees']):
        raise ValueError('ADP roster order changed')
    for key, row in saved['employees'].items():
        if row[:6] != template['employees'][key][:6]:
            raise ValueError('ADP identity/rate fields changed')
        person = mappings.get(key)
        expected = amounts(person)
        if row[6:] != expected:
            raise ValueError('ADP employee hours or tips changed during export')
    totals = {}
    for i, key in enumerate(('regular_hours', 'overtime_hours', 'tips')):
        total = sum(cents(r[6 + i], key) for r in saved['employees'].values() if r[6 + i] != '')
        expected = sum(cents(p[key], key) for p in people if p[key] is not None)
        totals[key] = decimal_text(total)
        if review:
            excluded = sum(cents(p[key], key) for p in missing if p[key] is not None)
            if total + excluded != expected:
                raise ValueError('EPI candidate plus named issues must account for every known amount')
        if total != expected:
            issues.append(f'{code}: {key} does not reconcile to TCP/Gateway')
    return content, {'region': code, 'filename': filename, 'ready': not issues,
                     'issues': issues, 'warnings': warnings, 'missing': missing,
                     'employees': audit_rows, 'totals': totals,
                     'template_sha256': hashlib.sha256(raw).hexdigest(),
                     'output_sha256': hashlib.sha256(content).hexdigest()}


def apply_tcp_rates(content, company, detail, rates):
    """Set each matched person's hourly rate in the EPI rows to their TCP rate.

    Only rows matched to a TCP person (detail['employees'] geid) with a TCP rate
    change, and only hourly or blank rates ('H ...' or ''); salary rows keep ADP's
    value. Control rows, order, names and amounts are unchanged. Returns new content
    and records the count in detail['tcp_rates'].
    """
    by_file = {row['file_number']: rates[row['geid']] for row in detail.get('employees', [])
               if row.get('geid') and (rates or {}).get(row['geid'])}
    encoding = 'utf-8-sig' if content.startswith(b'\xef\xbb\xbf') else 'utf-8'
    try:
        text = content.decode(encoding)
    except UnicodeDecodeError:
        encoding, text = 'cp1252', content.decode('cp1252')
    lines = text.splitlines(keepends=True)
    changed = differed = 0
    for i, line in enumerate(lines):
        body = line.rstrip('\r\n')
        if not body.startswith(company + ','):
            continue
        row = next(csv.reader([body]))
        if len(row) != 9 or row[2] not in by_file or not (row[5] == '' or row[5].startswith('H ')):
            continue
        new = 'H ' + by_file[row[2]]
        if row[5] != new:
            differed += 1
        changed += 1
        buffer = io.StringIO(newline='')
        csv.writer(buffer, lineterminator='').writerow(row[:5] + [new] + row[6:])
        lines[i] = buffer.getvalue() + line[len(body):]
    detail['tcp_rates'] = {'rows_set': changed, 'differed_from_adp_export': differed}
    if changed:
        detail.setdefault('warnings', []).append(
            f'{detail.get("region", company)}: hourly rate from TCP for {changed} matched people '
            f'({differed} differed from the ADP export).')
    return ''.join(lines).encode(encoding)


def record_roster_notes(detail, settings, code):
    """Copy one region's live-roster and confirmed-identity notes into its readiness detail.

    payroll_run.prepare supplies settings['roster_notes'][code] =
    {'roster_notes': [...], 'identity_notes': [...], 'adp_added': [...]}. These
    optional steps are recorded as warnings only: an unavailable ADP roster or a
    rejected identity list is visible but never changes readiness or blocks
    delivery (a person it leaves unmatched is already an issue of fill_region).
    Never raises.
    """
    notes = {'roster_notes': [], 'identity_notes': [], 'adp_added': []}
    try:
        extra = (settings.get('roster_notes') or {}).get(code) or {}
        listed = lambda key: extra.get(key) if isinstance(extra.get(key), list) else []
        for key in ('roster_notes', 'identity_notes'):
            notes[key] = [n for n in listed(key) if isinstance(n, str)]
        notes['adp_added'] = [{k: str(row.get(k, '')) for k in ('file_number', 'name', 'rate', 'hire_date')}
                              for row in listed('adp_added') if isinstance(row, dict)]
    except Exception:
        notes = {'roster_notes': [], 'identity_notes': [], 'adp_added': []}
    detail.update(notes)
    detail['warnings'] = notes['roster_notes'] + notes['identity_notes'] + detail['warnings']


def build_imports(summary, template_folder, output, settings=None):
    settings = settings or {}
    output.mkdir(parents=True, exist_ok=True)
    audit = {'period': summary['period'], 'ready': True, 'regions': {}, 'issues': [], 'missing': []}
    for person in summary['employees']:
        if not person.get('stores'):
            audit['missing'].append(dict(region='UNASSIGNED', geid=person['geid'],
                name=person['first_name'] + ' ' + person['last_name'], regular_hours=person['regular_hours'],
                overtime_hours=person['overtime_hours'], tips=person['tips'],
                reason='No home region or ADP identity; review for addition or termination'))
    for region, (code, _, filename) in FILES.items():
        source = template_folder / filename
        if not source.is_file():
            audit['issues'].append(f'{code}: missing current ADP template {filename}')
            continue
        content, detail = fill_region(summary, region, source.read_bytes(),
                                     settings.get('identities', {}).get(code), settings.get('allow_missing_zero', False))
        content = apply_tcp_rates(content, FILES[region][1], detail, settings.get('tcp_rates'))
        record_roster_notes(detail, settings, code)
        audit['regions'][code] = detail
        audit['issues'].extend(detail['issues'])
        audit['missing'].extend(detail['missing'])
        # Keep incomplete candidates away from the importable output folder.
        folder = output if detail['ready'] else output / 'NEEDS REVIEW'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_bytes(content)
        detail['import_format'] = validate_import(region, filename, (folder / filename).read_bytes())
    audit['ready'] = not audit['issues'] and len(audit['regions']) == 3
    checks = [f"ADP import checks: {summary['period']['start_date']} through {summary['period']['end_date']}",
              'Upload the exact EPI filename ending in .CSV. Do not upload an .xlsx file or rename one to .CSV.',
              'Select the matching ADP company and pay period. Do not import a batch twice.',
              'Check the import results for rejected rows, then compare batch totals and employee file numbers.',
              'The amounts below are CSV totals; supporting store reports include employees listed in issues.', '']
    for region, (code, company, filename) in FILES.items():
        detail = audit['regions'].get(code)
        if not detail:
            continue
        totals = detail['totals']
        checks.append(f"{code} | Company {company} | {filename} | {len(detail['employees'])} employee rows")
        checks.append(f"Regular hours: {totals['regular_hours']} | Overtime hours: {totals['overtime_hours']} | Tips: ${totals['tips']}")
        if not detail['ready']:
            checks.append('INCOMPLETE: resolve the employee matches on the payroll issues sheet before finalizing payroll.')
        checks.extend('Live ADP roster note: ' + note for note in detail.get('roster_notes', []))
        if detail.get('adp_added'):
            checks.append('Rows added from the live ADP roster (not on the last export): ADP file '
                          + ', '.join(row['file_number'] for row in detail['adp_added']))
        checks.extend('Identity note: ' + note for note in detail.get('identity_notes', []))
        checks.append('')
    (output / 'ADP import checks.txt').write_text('\n'.join(checks), encoding='utf-8')
    return audit
