"""Private role reviews and owner-confirmed payroll overrides; no TCP writes."""
import json
import os
from datetime import date

from allocate_tips import MANAGEMENT


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
        periods = {}
        for item in confirmed:
            if (not isinstance(item, dict) or item.get('geid') not in ids
                    or item.get('role') not in MANAGEMENT
                    or not isinstance(item.get('evidence_ref'), str) or not item['evidence_ref'].strip()
                    or not isinstance(item.get('period'), dict)):
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


def confirmed_roles_for_period(period):
    try:
        value = registry()
    except ValueError:
        return {}  # apply_role_reviews adds the global configuration blocker.
    return {item['geid']: item['role'] for item in value.get('confirmed', []) if item['period'] == period}


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
    for item in value.get('confirmed', []):
        if item['period'] != payroll['period'] or item['geid'] not in people:
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
        payroll.setdefault('warnings', []).append(
            f'GEID {item["geid"]}: owner-confirmed {item["role"]} for this payroll period; '
            'excluded from tips. TCP source title retained in audit; no TCP record changed.')
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
