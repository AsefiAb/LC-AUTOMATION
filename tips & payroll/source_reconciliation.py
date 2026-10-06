"""Compare independently obtained Altametrics totals; never merge or invent hours."""
import hashlib
import json
from collections import defaultdict
from datetime import date

from allocate_tips import cents, decimal_text

FIELDS = ('regular_hours', 'overtime_hours')
TCP_AUTHORITATIVE_FROM = date(2026, 9, 22)


def source_policy(period):
    """Owner confirmed on 2026-10-06 that staff clock directly in TCP.

    Applies to Sep 22-Oct 5 and later payrolls, not the disputed Sep 8-21
    historical payroll. This policy changes only the hours source; it does not
    clear TCP punch/role errors or ADP identity/import controls.
    """
    current = date.fromisoformat(period['start_date']) >= TCP_AUTHORITATIVE_FROM
    return {'hours_source': 'TCP', 'effective_from': TCP_AUTHORITATIVE_FROM.isoformat(),
            'independent_hours_comparison_required': not current,
            'basis': ('Owner confirmed employees clock in TCP; Altametrics is informational'
                      if current else 'Historical period still requires independent reconciliation')}


def hours_fingerprint(payroll):
    rows = sorted((p['store'], str(p['employee_id']),
                   *(decimal_text(cents(p[f], f)) for f in FIELDS))
                  for p in payroll['employees'])
    return hashlib.sha256(json.dumps({'period': payroll['period'], 'rows': rows},
                                    sort_keys=True).encode()).hexdigest()


def reconcile(payroll, stores, evidence=None):
    stores = list(stores)
    policy = source_policy(payroll['period'])
    comparison = compare_altametrics(payroll, stores, evidence)
    if policy['independent_hours_comparison_required']:
        return {**comparison, 'source_policy': policy}
    return {'ready': True, 'period': payroll['period'], 'source_policy': policy,
            'tcp_hours_sha256': hours_fingerprint(payroll),
            'stores': {s: {'ready': True, 'issues': []} for s in sorted(stores)},
            'informational_comparison': comparison}


def compare_altametrics(payroll, stores, evidence=None):
    """Evidence is private, period-specific, independently collected, and GEID-mapped.

    A coverage declaration is not a substitute for collecting the entire source
    report. Retain that report at each evidence_ref. Different totals require
    investigation, not choosing the larger total or adding both systems.
    """
    stores = set(stores)
    result = {'ready': False, 'period': payroll['period'],
              'tcp_hours_sha256': hours_fingerprint(payroll), 'stores': {}}
    def fail_all(reason):
        result['stores'] = {s: {'ready': False, 'issues': [reason]} for s in sorted(stores)}
        return result
    if not evidence:
        return fail_all('Altametrics hours have not been reconciled with TCP for this period.')
    if (evidence.get('source') != 'Altametrics' or evidence.get('period') != payroll['period']
            or evidence.get('tcp_hours_sha256') != result['tcp_hours_sha256']):
        return fail_all('Source comparison is stale, for different dates, or not tied to these TCP hours.')
    coverage = evidence.get('coverage', {})
    records = evidence.get('records')
    if not isinstance(coverage, dict) or not isinstance(records, list):
        return fail_all('Source comparison is missing its coverage or employee records.')
    expected, actual, errors = {}, {}, defaultdict(list)
    roster = {str(p['geid']) for p in payroll['roster']}
    try:
        for p in payroll['employees']:
            key = (p['store'], str(p['employee_id']))
            if key in expected:
                return fail_all('Duplicate TCP employee/store totals; comparison cannot be trusted.')
            expected[key] = tuple(cents(p[f], f) for f in FIELDS)
        for p in records:
            store, geid = p['store'], p['geid']
            if store not in stores:
                return fail_all('Source comparison contains an unknown store.')
            if not isinstance(geid, str) or not geid.strip() or geid not in roster:
                errors[store].append('Altametrics employee has no verified TCP GEID; resolve identity before payroll.')
                continue
            key = (store, geid)
            if key in actual:
                errors[store].append(f'GEID {geid}: duplicate Altametrics employee/store totals.')
                continue
            actual[key] = tuple(cents(p[f], f) for f in FIELDS)
        for key in sorted(set(expected) | set(actual)):
            tcp, alt = expected.get(key, (0, 0)), actual.get(key, (0, 0))
            if tcp != alt:
                errors[key[0]].append(
                    f'GEID {key[1]}: TCP regular/OT {decimal_text(tcp[0])}/{decimal_text(tcp[1])}; '
                    f'Altametrics regular/OT {decimal_text(alt[0])}/{decimal_text(alt[1])}. '
                    'Reconcile punches, breaks and overtime; do not add both totals.')
    except (KeyError, TypeError, ValueError):
        return fail_all('Source comparison has invalid employee identifiers or decimal hours.')
    for store in sorted(stores):
        spec = coverage.get(store, {})
        if (not isinstance(spec, dict) or spec.get('complete') is not True
                or not isinstance(spec.get('evidence_ref'), str) or not spec['evidence_ref'].strip()):
            errors[store].insert(0, 'Complete Altametrics employee coverage and a source reference are required.')
        result['stores'][store] = {'ready': not errors[store], 'issues': errors[store]}
    result['ready'] = all(s['ready'] for s in result['stores'].values())
    return result


def completion_code(audit):
    """Successful email transport is not successful payroll reconciliation."""
    return 0 if audit.get('ready') is True and audit.get('reports_deliverable') is True else 2
