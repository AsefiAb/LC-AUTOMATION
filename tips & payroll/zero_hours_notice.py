"""One email per payroll period listing employees with 0 hours, for termination review.

To the district managers (Arizona: Jeff, Colorado: Caleb, Texas: Wilder) and
Kerry in a single message, cc Alicia. Read-only: nothing is changed in TCP or ADP.

Per region, every list sorted by last name then first name:
  - zero_hours: active ADP employees with no TCP time punches in the period. A
    person counts as having hours if TCP shows any punch for them in the period,
    even one with zero calculated hours, so missing calculations never cause a
    false listing.
  - tcp_only: people still active in TCP with no punches whose ADP record is
    terminated, missing, or active only under another region's ADP company.
  - check_names: ADP records that may belong to a TCP person who worked under
    another spelling or in another region. Asked about, never counted as a match.
  - same_name: several active ADP records share one name and fewer TCP people
    with that name worked, so it is unclear which record has 0 hours.
  - duplicates: one ADP person with more than one active ADP record; payroll
    uses the most recent one (adp_api.current_workers) and the DM is asked to
    close the extra one.
People hired during the period are left out.

TCP and ADP people are matched only by exact name (case and spacing aside) or
by an owner-confirmed GEID -> ADP file number match. A confirmation is used
only when the TCP person exists with the confirmed TCP name and the confirmed
ADP file is active under the confirmed ADP name; a confirmed older assignment
moves to the same ADP person's newest one. Anything else is reported in notes
and never guessed. Name similarity only routes a record to check_names or adds
a note; it never makes a match.
"""
import difflib
import re
from collections import Counter
from email.message import EmailMessage

import adp_api
from adp_identities import valid_entry
from adp_roster import name_forms, name_key
from payroll_email_summary import date_label
from summarize_inspection import store_label

REGIONS = (('AZ', 'Arizona', 'FNC', '3759', 'Jeff'),
           ('CO', 'Colorado', 'FND', '3356', 'Caleb'),
           ('TX', 'Texas', 'ZKT', '3910', 'Wilder'))
TO = ['jeff@caazllc.com', 'caleb@lccollc.com', 'wilder@lccollc.com', 'kerry@lccollc.com']
CC = ['alicia@lccollc.com']
SENDER = 'ab@caazllc.com'
SIMILAR = 0.85
# District, office and administrative staff are salaried and do not punch in TCP.
NON_STORE_TITLES = {'district manager', 'admin', 'transaction manager'}
FRANCHISES = {franchise for *_, franchise, _ in REGIONS}
REGION_NAME = {code: name for code, name, *_ in REGIONS}
COMPANY_REGION = {company: code for code, _, company, _, _ in REGIONS}
LISTS = ('zero_hours', 'tcp_only', 'check_names', 'same_name', 'duplicates')


def key(value):
    return name_key(str(value or ''))


def file_key(number):
    return str(number or '').strip().lstrip('0')


def adp_store(location):
    """ADP home location such as 37596 -> store 3759-06; anything else (offices) -> ''."""
    match = re.fullmatch(r'(\d{4})(\d{1,2})', str(location or '').strip())
    return f'{match[1]}-{int(match[2]):02d}' if match and match[1] in FRANCHISES else ''


def store_staff(worker):
    """Hourly store employees and store managers; excludes district/office staff."""
    if key(worker.get('job_title') or '') in NON_STORE_TITLES:
        return False
    location = str(worker.get('location') or '').strip()
    return not location or bool(adp_store(location))


def tcp_region(person):
    role = str(person.get('roleId') or '').upper()
    if role in ('AZ', 'CO', 'TX'):
        return role
    store = store_label(person.get('location'))
    return next((code for code, _, _, franchise, _ in REGIONS if store.startswith(franchise + '-')), '')


def tcp_sources_complete(report):
    datasets = report.get('datasets', {})
    return (not report.get('errors')
            and all(datasets.get(name, {}).get('complete') for name in ('employees', 'worksegments')))


def worked_ids(report):
    first, last = report['period']['startDate'], report['period']['stopDate']
    return {str(row['employeeId']) for row in report['datasets']['worksegments']['records']
            if first <= str(row.get('timeIn') or '')[:10] <= last}


def tcp_people(report):
    people = {}
    for person in report['datasets']['employees']['records']:
        geid = str(person['employeeId'])
        first, last = str(person.get('firstName') or '').strip(), str(person.get('lastName') or '').strip()
        people[geid] = {'geid': geid, 'first_name': first, 'last_name': last, 'name': f'{first} {last}'.strip(),
                        'region': tcp_region(person), 'store': store_label(person.get('location')),
                        'hire_date': str(person.get('hireDate') or '')[:10],
                        'termination_date': str(person.get('terminationDate') or '')[:10],
                        'suspended': bool(person.get('isSuspended'))}
    return people


def adp_names(worker):
    names = {key(worker['first_name'] + ' ' + worker['last_name'])}
    if worker.get('preferred_name'):
        names.add(key(worker['preferred_name'] + ' ' + worker['last_name']))
    return names


def similar(a, b):
    return difflib.SequenceMatcher(None, key(a), key(b)).ratio() >= SIMILAR


def tokens(value):
    return {t for t in re.split(r'[\s\-]+', key(value)) if t}


def maybe_same(person, worker):
    """Routing only (to 'please confirm' or a note), never an identity match.

    True when the full names are similar, or when the last names share a word
    (or are similar) and the first names share a word or the first initial, which
    catches middle names, compound surnames and short forms.
    """
    if any(similar(person['name'], name) for name in adp_names(worker)):
        return True
    last = (bool(tokens(person['last_name']) & tokens(worker['last_name']))
            or similar(person['last_name'], worker['last_name']))
    preferred = worker.get('preferred_name') or ''
    initial = key(person['first_name'])[:1]
    first = (bool(tokens(person['first_name']) & (tokens(worker['first_name']) | tokens(preferred)))
             or (initial != '' and initial in (key(worker['first_name'])[:1], key(preferred)[:1])))
    return last and first


def order(row):
    return adp_api.sort_key(row['last_name'], row['first_name']) + (row.get('file_number', ''), row.get('geid', ''))


def adp_row(worker, **extra):
    return {'name': f"{worker['first_name']} {worker['last_name']}", 'first_name': worker['first_name'],
            'last_name': worker['last_name'], 'file_number': worker['file_number'],
            'hire_date': worker['hire_date'], **extra}


def resolve_identities(identities, people, per_company, records_by_file, notes):
    """Sort each confirmation into applied (linked to a current ADP record) or not, with a note.

    Returns (links, applied, stale, hints, missing_tcp):
      links: (company, file key of a current record) -> confirmed GEIDs
      stale: GEID -> (company, confirmed file number, 'terminated' | 'missing');
             such a GEID is never matched to an ADP record by name
      hints: (company, file key) -> GEIDs whose TCP name changed since the
             confirmation; used only to ask, never as a match
      missing_tcp: (company, file key) -> confirmed GEID that TCP no longer has
    """
    links, applied, stale, hints, missing_tcp = {}, set(), {}, {}, {}
    seen_geids, seen_files = set(), set()
    for item in identities or ():
        if not valid_entry(item):
            notes.append('A confirmed ADP identity entry is malformed; it was not used.')
            continue
        geid, company, number = item['geid'], item['company'], item['file_number']
        code, number_key = COMPANY_REGION[company], file_key(number)
        tag = f'{code} GEID {geid}: confirmed ADP file {number}'
        if geid in seen_geids or (company, number_key) in seen_files:
            notes.append(f'{tag} repeats another confirmed match for the same GEID or ADP file; not used.')
            continue
        seen_geids.add(geid)
        seen_files.add((company, number_key))
        current, older = per_company[company]
        record = (next((w for w in current if file_key(w['file_number']) == number_key), None)
                  or next((w for w in older if file_key(w['file_number']) == number_key), None))
        target = record if record is None or 'replaced_by' not in record else next(
            (w for w in current if w['file_number'] == record['replaced_by']), None)
        if record is None or target is None:
            if geid in people:
                status = 'terminated' if records_by_file.get((company, number_key)) else 'missing'
                stale[geid] = (company, number, status)
                notes.append(f'{tag} is terminated or not active in ADP; match not applied.' if status == 'terminated'
                             else f'{tag} is not in ADP; match not applied.')
            else:
                notes.append(f'{code} GEID {geid}: confirmed TCP ID is not in the TCP employee list, and confirmed '
                             f'ADP file {number} is not active in ADP; match not applied.')
            continue
        target_key = (company, file_key(target['file_number']))
        if geid not in people:
            notes.append(f'{code} GEID {geid}: confirmed TCP ID is not in the TCP employee list; match not applied.')
            missing_tcp[target_key] = geid
            continue
        # A saved ADP_ROSTER_SETTINGS match carries the EPI roster spelling, which can hold
        # ADP's own alternate name in parentheses; payroll accepts that row, so does this.
        if not name_forms(item['adp_name']) & adp_names(record):
            notes.append(f'{tag} no longer has the confirmed ADP name in ADP; match not applied.')
            continue
        if key(item['tcp_name']) != key(people[geid]['name']):
            notes.append(f'{code} GEID {geid}: the TCP name no longer agrees with the confirmed TCP name; '
                         'match not applied.')
            hints.setdefault(target_key, set()).add(geid)
            continue
        if record is not target:
            notes.append(f'{tag} is an older assignment of the same ADP person; using the newest ADP file '
                         f"{target['file_number']}.")
        if links.get(target_key):
            notes.append(f"{code}: more than one confirmed TCP ID points at ADP file {target['file_number']}.")
        links.setdefault(target_key, set()).add(geid)
        applied.add(geid)
    return links, applied, stale, hints, missing_tcp


def build(dates, report, workers, identities=(), notes=None):
    """Return {region: {list name: [rows]}} for the email; no I/O.

    Notes about confirmations that were not used (and other things to check
    that are not for the DMs) are appended to `notes` when a list is given.
    """
    notes = [] if notes is None else notes
    start, end = dates['start_date'], dates['end_date']
    worked = worked_ids(report)
    people = tcp_people(report)
    per_company = {company: adp_api.current_workers(workers, company) for _, _, company, _, _ in REGIONS}
    records_by_file, inactive_by_name, active_by_name = {}, {}, {}
    for worker in workers:
        company = worker['company_code'].upper()
        if file_key(worker['file_number']):
            records_by_file.setdefault((company, file_key(worker['file_number'])), []).append(worker)
        if not adp_api.is_active(worker):
            for name in adp_names(worker):
                inactive_by_name.setdefault(name, set()).add(company)
    for company, (current, _) in per_company.items():
        for worker in current:
            for name in adp_names(worker):
                active_by_name.setdefault(name, {}).setdefault(company, []).append(worker['file_number'])

    links, applied, stale, hints, missing_tcp = resolve_identities(identities, people, per_company,
                                                                   records_by_file, notes)
    home = {g: p['region'] or (COMPANY_REGION[stale[g][0]] if g in stale else '') for g, p in people.items()}
    # Exact-name candidates: confirmed people are already placed, and a person whose
    # confirmed ADP record is gone is never paired with an unrelated record by name.
    by_name = {}
    for geid, person in people.items():
        if geid not in applied and geid not in stale and person['name']:
            by_name.setdefault(key(person['name']), set()).add(geid)

    # Pass 1: exact-name and confirmed matches for every current ADP record in every
    # company, so the lookalike checks never pair someone who already has a record.
    matches, matched = {}, set(applied)
    for code, _, company, _, _ in REGIONS:
        current, _ = per_company[company]
        unconfirmed_names = {n for w in current if not links.get((company, file_key(w['file_number'])))
                             for n in adp_names(w)}
        rows = []
        for worker in current:
            linked = links.get((company, file_key(worker['file_number'])), set())
            geids = set(linked)
            for name in adp_names(worker):
                if not linked or name not in unconfirmed_names:
                    geids |= {g for g in by_name.get(name, ()) if people[g]['region'] == code}
            rows.append((worker, geids, bool(linked)))
            matched |= geids
        matches[company] = rows

    def tcp_label(geid):
        region = people[geid]['region']
        return f'{REGION_NAME[region]} TCP' if region else 'TCP (region unknown)'

    def lookalike_note(geid):
        if geid in stale:
            _, number, status = stale[geid]
            return (f"TCP person's confirmed ADP #{number} is terminated in ADP" if status == 'terminated'
                    else f"TCP person's confirmed ADP #{number} is not in ADP")
        return ''

    result = {}
    for code, region_name, company, _, _ in REGIONS:
        current, older = per_company[company]
        rows = matches[company]
        in_region = {g: p for g, p in people.items() if home[g] == code}
        pool = {g: p for g, p in in_region.items() if g not in matched and p['name']}
        names_n = Counter(n for w, _, linked in rows if not linked for n in adp_names(w))
        unmatched = [w for w, geids, _ in rows if not geids]
        zero, check, same, duplicates, tcp_only, named = [], [], [], [], [], set()
        for worker, geids, linked in rows:
            if worker['hire_date'] and worker['hire_date'] >= start:
                continue  # Hired during this period.
            if not store_staff(worker):
                continue
            names = adp_names(worker)
            number_key = file_key(worker['file_number'])
            store = next((people[g]['store'] for g in sorted(geids) if people[g]['store'].count('-') == 1), '')
            did_work = geids & worked
            shared = 0 if linked else max(names_n[n] for n in names)
            if shared > 1 and 0 < len(did_work) < shared:
                # Several active ADP records share this name and fewer of them worked: ask, never guess.
                same.append(adp_row(worker, store=adp_store(worker['location']), records=shared,
                                    worked=len(did_work), tcp_ids=sorted(geids)))
                continue
            if did_work:
                continue
            elsewhere = sorted({g for n in names for g in by_name.get(n, ()) if home[g] != code})
            if geids:
                others = sorted({tcp_label(g) for g in elsewhere if g in worked})
                zero.append(adp_row(worker, store=store or adp_store(worker['location']),
                                    note='same name worked in ' + ' / '.join(others) if others else ''))
                continue
            lookalikes = [p for g, p in sorted(pool.items()) if maybe_same(p, worker)]
            lookalikes += [people[g] for g in sorted(hints.get((company, number_key), ()))
                           if g not in matched and people[g] not in lookalikes]
            named |= {p['geid'] for p in lookalikes}
            asks = [(p, lookalike_note(p['geid']) or ('TCP name changed since the saved match'
                                                      if p['geid'] in hints.get((company, number_key), ()) else ''))
                    for p in lookalikes if p['geid'] in worked]
            for geid in elsewhere:
                if any(p['geid'] == geid for p, _ in asks):
                    continue
                if geid in worked and not people[geid]['region']:
                    asks.append((people[geid], 'TCP region unknown'))
                elif geid in worked and geid not in matched:
                    other = REGION_NAME[people[geid]['region']]
                    asks.append((people[geid], f'same name worked in {other} TCP; not in {other} ADP'))
            if asks:
                # Possibly the same person who did work: ask, never report 0 hours.
                check.extend({'adp_name': f"{worker['first_name']} {worker['last_name']}",
                              'file_number': worker['file_number'], 'tcp_name': p['name'], 'geid': p['geid'],
                              'store': p['store'], 'last_name': worker['last_name'],
                              'first_name': worker['first_name'], 'note': note} for p, note in asks)
                continue
            parts = []
            if lookalikes:
                parts.append('in TCP as ' + ' / '.join(p['name'] for p in lookalikes))
            if elsewhere:
                worked_where = sorted({tcp_label(g) for g in elsewhere if g in worked})
                parts.append(f'not in {region_name} TCP; same name ' + (
                    'worked in ' + ' / '.join(worked_where) if worked_where
                    else 'in ' + ' / '.join(sorted({tcp_label(g) for g in elsewhere}))))
            if not parts:
                parts.append('not found in TCP')
            if (company, number_key) in missing_tcp:
                parts.append(f'confirmed TCP ID {missing_tcp[(company, number_key)]} is not in TCP')
            zero.append(adp_row(worker, store=adp_store(worker['location']), note='; '.join(parts)))
        current_by_file = {w['file_number']: w for w in current}
        for worker in older:
            if (worker['hire_date'] and worker['hire_date'] >= start) or not store_staff(worker):
                continue
            newest = current_by_file.get(worker['replaced_by'], {})
            # Neutral wording: the records may be concurrent, and the flags ADP sets do not say which is older.
            duplicates.append(adp_row(worker, store=adp_store(worker['location']),
                                      current_file=worker['replaced_by'],
                                      current_store=adp_store(newest.get('location')),
                                      current_hire=newest.get('hire_date', ''),
                                      note=f"more than one active ADP record; payroll uses #{worker['replaced_by']}"))
        active_here = set().union(*(adp_names(w) for w in current)) if current else set()
        for geid, person in in_region.items():
            if (geid in matched or geid in worked or person['suspended']
                    or (person['termination_date'] and person['termination_date'] <= end)
                    or (person['hire_date'] and person['hire_date'] >= start) or not person['name']):
                continue
            row = {'name': person['name'], 'first_name': person['first_name'], 'last_name': person['last_name'],
                   'geid': geid, 'store': person['store']}
            if geid in stale:
                # Owner-confirmed ADP record is gone: listed even though the name may resemble someone else.
                _, number, status = stale[geid]
                tcp_only.append({**row, 'note': f'terminated in ADP (#{number})' if status == 'terminated'
                                 else f'confirmed ADP #{number} not found in ADP'})
                continue
            name = key(person['name'])
            if name in active_here or geid in named:
                continue  # Has an active ADP record here, or is already named in an ADP row above.
            if any(similar(person['name'], n) for w in unmatched for n in adp_names(w)):
                continue  # Likely the same person under another spelling; never reported as terminated.
            elsewhere = {c: files for c, files in active_by_name.get(name, {}).items() if c != company}
            terminated = inactive_by_name.get(name, set())
            if elsewhere:
                note = f'not in {region_name} ADP; active in ADP under ' + ', '.join(
                    f"{REGION_NAME[COMPANY_REGION[c]]} #{' / #'.join(sorted(files))}"
                    for c, files in sorted(elsewhere.items()))
            elif company in terminated:
                note = 'terminated in ADP'
            elif terminated & set(COMPANY_REGION):
                note = 'terminated in ADP under ' + ' / '.join(
                    sorted(REGION_NAME[COMPANY_REGION[c]] for c in terminated if c in COMPANY_REGION))
            else:
                note = 'not found in ADP'
            tcp_only.append({**row, 'note': note})
        result[code] = {'zero_hours': sorted(zero, key=order), 'tcp_only': sorted(tcp_only, key=order),
                        'check_names': sorted(check, key=lambda r: order(r) + (key(r['tcp_name']),)),
                        'same_name': sorted(same, key=order), 'duplicates': sorted(duplicates, key=order)}
    unplaced = sorted(g for g, p in people.items()
                      if not home[g] and p['name'] and g not in matched and g not in worked and not p['suspended']
                      and not (p['termination_date'] and p['termination_date'] <= end)
                      and not (p['hire_date'] and p['hire_date'] >= start))
    if unplaced:
        notes.append(f'{len(unplaced)} TCP people with no punches have no region (role or store) in TCP and were '
                     'not checked against ADP: TCP IDs ' + ', '.join(unplaced) + '.')
    return result


def build_message(dates, report, workers, identities=(), notes=None):
    """(message, data): the plain-text email, no attachments, and the lists it was built from."""
    data = build(dates, report, workers, identities, notes)
    label = date_label(dates)
    message = EmailMessage()
    message['From'] = SENDER
    message['To'] = ', '.join(TO)
    message['Cc'] = ', '.join(CC)
    message['Subject'] = f'Employees with 0 hours - {label}'
    message['Message-ID'] = f'<payroll-zero-hours-{dates["start_date"]}-{dates["end_date"]}@caazllc.com>'
    lines = ['Hi all,', '',
             f'These employees have 0 hours for the last two weeks ({label}). '
             'Please send over termination sheets if they are not employed.', '']

    def detail(*parts):
        return '- ' + ', '.join(p for p in parts if p)

    for code, name, _, _, manager in REGIONS:
        rows = data[code]['zero_hours']
        lines.append(f'{name.upper()} ({manager})')
        if not rows:
            lines.append('- No employees with 0 hours.')
        for row in rows:
            lines.append(detail(row['name'], f"ADP #{row['file_number']}", row['store'] and f"store {row['store']}",
                                row['hire_date'] and f"hired {row['hire_date']}", row['note']))
        lines.append('')

    def section(kind, heading, render):
        rows = [(name, row) for code, name, *_ in REGIONS for row in data[code][kind]]
        if rows:
            lines.append(heading)
            lines.extend(render(name, row) for name, row in rows)
            lines.append('')

    def record(number, store, hired):
        extra = ', '.join(p for p in (store and f'store {store}', hired and f'hired {hired}') if p)
        return f'ADP #{number}' + (f' ({extra})' if extra else '')

    section('tcp_only', 'Also 0 hours and still active in TCP, but terminated or not found in ADP. '
                        'Please remove them from TCP if they no longer work for us, or send over their paperwork:',
            lambda name, row: detail(row['name'], name, f"TCP ID {row['geid']}",
                                     row['store'] and f"store {row['store']}", row['note']))
    section('duplicates', 'These people have more than one active ADP record (they may be working under the other '
                          'one). Please tell us which record is current and send a termination sheet for the extra '
                          'one, or let us know if both are needed:',
            lambda name, row: f"- {row['name']}, {name}: {record(row['file_number'], row['store'], row['hire_date'])}"
                              f" and {record(row['current_file'], row['current_store'], row['current_hire'])}; "
                              f"payroll uses #{row['current_file']}")
    section('same_name', 'Several active ADP records share the same name, and fewer people with that name worked '
                         'in TCP, so we cannot tell which record has 0 hours. Please tell us which of these ADP file '
                         'numbers still work:',
            lambda name, row: detail(row['name'], name, f"ADP #{row['file_number']}",
                                     row['store'] and f"store {row['store']}",
                                     row['hire_date'] and f"hired {row['hire_date']}",
                                     f"{row['records']} active ADP records with this name, "
                                     f"{row['worked']} worked in TCP"))
    section('check_names', 'Not counted above because the name is spelled differently in ADP and TCP, or the TCP '
                           'person is in another region. Please confirm whether these are the same person:',
            lambda name, row: f"- ADP {row['adp_name']} (#{row['file_number']}, {name}) and TCP {row['tcp_name']} "
                              f"(ID {row['geid']}{', store ' + row['store'] if row['store'] else ''})"
                              + (f"; {row['note']}" if row['note'] else ''))
    lines.extend(['People hired during these two weeks are not included.', '', 'Thanks,', 'AB'])
    message.set_content('\n'.join(lines))
    return message, data
