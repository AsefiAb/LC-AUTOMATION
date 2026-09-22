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


def fill_region(summary, region, raw, overrides=None, allow_missing_zero=False):
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
    people = regional_people(summary)[region]
    names = defaultdict(list)
    for target, row in template['employees'].items():
        names[name_key(row[4])].append(target)
        # ADP itself explicitly supplies the alternate name in parentheses.
        # This is not fuzzy matching or a guessed nickname.
        for alias in re.findall(r'\(([^()]+)\)', row[4]):
            if target not in names[name_key(alias)]:
                names[name_key(alias)].append(target)
    mappings, used, issues, warnings, missing = {}, set(), [], [], []
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
                continue
            matches = [target]
        else:
            matches = names.get(name_key(fullname), [])
        if len(matches) != 1 or matches[0] in used:
            zero = all(cents(person[k], k) == 0 for k in ('regular_hours', 'overtime_hours', 'tips'))
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
        values = [person[k] for k in ('regular_hours', 'overtime_hours', 'tips')] if person else ['0.00'] * 3
        if not person:
            warnings.append(f'{code} ADP file {row[2]}: no matched TCP work in this region; retained at zero')
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
        expected = [person[k] for k in ('regular_hours', 'overtime_hours', 'tips')] if person else ['0.00'] * 3
        if row[6:] != expected:
            raise ValueError('ADP employee hours or tips changed during export')
    totals = {}
    for i, key in enumerate(('regular_hours', 'overtime_hours', 'tips')):
        total = sum(cents(r[6 + i], key) for r in saved['employees'].values())
        expected = sum(cents(p[key], key) for p in people)
        totals[key] = decimal_text(total)
        if total != expected:
            issues.append(f'{code}: {key} does not reconcile to TCP/Gateway')
    return content, {'region': code, 'filename': filename, 'ready': not issues,
                     'issues': issues, 'warnings': warnings, 'missing': missing,
                     'employees': audit_rows, 'totals': totals,
                     'template_sha256': hashlib.sha256(raw).hexdigest(),
                     'output_sha256': hashlib.sha256(content).hexdigest()}


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
        audit['regions'][code] = detail
        audit['issues'].extend(detail['issues'])
        audit['missing'].extend(detail['missing'])
        # Keep incomplete candidates away from the importable output folder.
        folder = output if detail['ready'] else output / 'NEEDS REVIEW'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_bytes(content)
    audit['ready'] = not audit['issues'] and len(audit['regions']) == 3
    return audit
