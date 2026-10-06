"""Private unresolved personnel-role checks; never change a TCP job title."""
import json
import os


def apply_role_reviews(payroll):
    """An explicit empty registry is required after every known review is closed.

    The registry lives in PAYROLL_ROLE_REVIEWS, not repository history. An open
    review remains blocking even if a later TCP title happens to match the other
    source: effective dates and identity still require a documented review.
    """
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
    except (ValueError, TypeError):
        payroll['issues'].append('Private role-review registry is missing or invalid. Review known role conflicts before allocating tips.')
        payroll['verified'] = False
        payroll['role_reviews'] = {'checked': False, 'unresolved': []}
        return payroll
    people = {str(p['geid']): p for p in payroll['roster']}
    unresolved = []
    for item in registry['unresolved']:
        person = people.get(item['geid'])
        if person is None:
            continue
        unresolved.append({**item, 'tcp_roles': person['roles']})
        payroll['issues'].append(
            f'GEID {item["geid"]}: unresolved job-title review. TCP roles: {", ".join(person["roles"])}; '
            f'independent source previously: {item["source_role"]}. Confirm the correct role and effective '
            'dates before allocating tips. No TCP title has been changed.')
    payroll['role_reviews'] = {'checked': True, 'unresolved': unresolved}
    if unresolved:
        payroll['verified'] = False
    return payroll
