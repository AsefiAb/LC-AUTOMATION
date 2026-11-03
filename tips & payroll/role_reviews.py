"""Private role reviews and owner-confirmed payroll overrides; no TCP writes."""
import json
import os
from datetime import date

from allocate_tips import CREW, MANAGEMENT


def registry():
    """Validate the entire private registry before using any confirmation."""
    text = os.environ.get('PAYROLL_ROLE_REVIEWS', '')
    try:
        registry = json.loads(text)
        if not isinstance(registry, dict) or registry.get('version') != 1 or not isinstance(registry.get('unresolved'), list):
            raise ValueError('Invalid registry')
        ids = set()
        for item in registry['unresolved']:
            if (not isinstance(item, dict) or not isinstance(item.get('geid'), str)
                    or not item['geid'].isdigit() or item['geid'] in ids
                    or not isinstance(item.get('source_role'), str) or not item['source_role'].strip()
                    or not isinstance(item.get('evidence_ref'), str) or not item['evidence_ref'].strip()):
                raise ValueError('Invalid review')
            ids.add(item['geid'])
        confirmed = registry.get('confirmed', [])
        if not isinstance(confirmed, list):
            raise ValueError('Invalid confirmed reviews')
        periods, standing = {}, set()
        for item in confirmed:
            # An owner confirmation may name crew or management for one exact period.
            if (not isinstance(item, dict) or not isinstance(item.get('geid'), str) or not item['geid'].isdigit()
                    or item.get('role') not in MANAGEMENT | CREW
                    # A crew confirmation never clears an open management-title review.
                    or (item.get('role') in CREW and item['geid'] in ids)
                    or not isinstance(item.get('evidence_ref'), str) or not item['evidence_ref'].strip()):
                raise ValueError('Invalid confirmed management review')
            if 'period' not in item:
                # Standing confirmation: applies from its start date for as long as
                # the person's TCP titles stay exactly the ones the owner confirmed.
                date.fromisoformat(item['from'])
                titles = item.get('tcp_titles')
                if (not isinstance(titles, list) or not titles
                        or not all(isinstance(t, str) and t.strip() for t in titles)):
                    raise ValueError('Standing confirmation needs the confirmed TCP titles')
                if item['geid'] in standing:
                    raise ValueError('Duplicate standing confirmation')
                standing.add(item['geid'])
                continue
            if not isinstance(item.get('period'), dict):
                raise ValueError('Invalid confirmed management review')
            first = date.fromisoformat(item['period']['start_date'])
            last = date.fromisoformat(item['period']['end_date'])
            if (last - first).days != 13:
                raise ValueError('Confirmed review must name one complete payroll period')
            previous = periods.setdefault(item['geid'], [])
            if any(first <= end and start <= last for start, end in previous):
                raise ValueError('Overlapping confirmed role periods')
            previous.append((first, last))
        return registry
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError('Invalid role-review registry') from exc


def effective(value, period, titles=None):
    """Confirmations for this period, and standing ones whose TCP titles have since changed.

    An exact-period confirmation wins. A standing one applies from its start date
    while the person's current TCP titles (titles: GEID -> titles) equal the
    confirmed ones; without titles it is not applied.
    """
    exact = {item['geid']: item for item in value.get('confirmed', []) if item.get('period') == period}
    applied, changed = dict(exact), []
    for item in value.get('confirmed', []):
        if 'period' in item or item['geid'] in exact or item['from'] > period['start_date']:
            continue
        current = sorted((titles or {}).get(item['geid'], []))
        if current == sorted(item['tcp_titles']):
            applied[item['geid']] = item
        elif titles is not None and item['geid'] in titles:
            changed.append({**item, 'current_titles': current})
    return applied, changed


def confirmed_roles_for_period(period, titles=None):
    try:
        value = registry()
    except ValueError:
        return {}  # apply_role_reviews adds the global configuration blocker.
    return {geid: item['role'] for geid, item in effective(value, period, titles)[0].items()}


def apply_role_reviews(payroll):
    """Apply an exact-period confirmation, preserving recorded TCP roles.

    An open review remains blocking outside explicitly confirmed periods. The
    effective payroll role changes locally; this function never writes to TCP.
    """
    try:
        value = registry()
    except ValueError:
        payroll['issues'].append('Private role-review registry is missing or invalid. Review known role conflicts before allocating tips.')
        payroll['verified'] = False
        payroll['role_reviews'] = {'checked': False, 'unresolved': []}
        return payroll
    people = {str(p['geid']): p for p in payroll['roster']}
    applied = []
    confirmations, changed = effective(value, payroll['period'], payroll.get('tcp_titles'))
    for item in changed:
        if item['geid'] in people:
            payroll['issues'].append(
                f'GEID {item["geid"]}: TCP titles changed since the owner confirmed {item["role"]} '
                f'({", ".join(item["tcp_titles"])} -> {", ".join(item["current_titles"]) or "none"}); '
                'confirm the role again before allocating tips.')
            payroll['verified'] = False
    for item in confirmations.values():
        if item['geid'] not in people:
            continue
        person = people[item['geid']]
        source_roles = list(person['roles'])
        person['tcp_roles'] = source_roles
        person['roles'] = [item['role']]
        for row in payroll['employees']:
            if str(row['employee_id']) == item['geid']:
                row['tcp_roles'] = list(row['roles'])
                row['roles'] = [item['role']]
        applied.append({**item, 'tcp_roles': source_roles})
        effect = 'excluded from tips' if item['role'] in MANAGEMENT else 'eligible for tips'
        payroll.setdefault('warnings', []).append(
            f'GEID {item["geid"]}: owner-confirmed {item["role"]}'
            f'{" for this payroll period" if "period" in item else " (standing; TCP titles unchanged)"}; '
            f'{effect}. TCP source title retained in audit; no TCP record changed.')
    resolved_ids = {item['geid'] for item in applied}
    unresolved = []
    for item in value['unresolved']:
        if item['geid'] in resolved_ids:
            continue
        person = people.get(item['geid'])
        if person is None:
            continue
        unresolved.append({**item, 'tcp_roles': person['roles']})
        payroll['issues'].append(
            f'GEID {item["geid"]}: unresolved job-title review. TCP roles: {", ".join(person["roles"])}; '
            f'independent source previously: {item["source_role"]}. Confirm the correct role and effective '
            'dates before allocating tips. No TCP title has been changed.')
    payroll['role_reviews'] = {'checked': True, 'unresolved': unresolved, 'confirmed': applied}
    if unresolved:
        payroll['verified'] = False
    return payroll
