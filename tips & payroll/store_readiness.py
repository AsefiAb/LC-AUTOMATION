"""Scope known source problems before allocating review-only store tip pools."""
from collections import defaultdict
from decimal import Decimal

from allocate_tips import allocate, CREW, MANAGEMENT
from source_reconciliation import reconcile
from summarize_inspection import summarize, store_label


def review_allocations(payroll, report, rows, tips, stores, comparison=None):
    for row in rows:
        row['tips'] = None  # Never carry prior shares across a newly blocked run.
    blocked = defaultdict(list)
    all_stores = set(stores)
    def block(reason, affected=None):
        for sid in (all_stores if affected is None else affected):
            blocked[sid].append(reason)
    result = {'period': (payroll or tips or {}).get('period'),
              'stores': {s: {'allocated': False, 'issues': []} for s in sorted(stores)}}
    if not payroll:
        block('Calculated TCP hours are unavailable.')
    if not tips or not tips.get('verified') or tips.get('issues'):
        block('Complete verified Gateway tip pools are unavailable.')
    if payroll:
        comparison = comparison or reconcile(payroll, stores)
        for sid, detail in comparison['stores'].items():
            if not detail['ready']:
                block('Historical independent hours reconciliation is incomplete.', {sid})
        person_stores = defaultdict(set)
        for row in rows:
            if row['store'] in all_stores:
                person_stores[row['geid']].add(row['store'])
        source = None
        if report:
            try:
                source = summarize(report)
            except (ValueError, KeyError, TypeError):
                block('TCP source audit is incomplete; store problems cannot be isolated.')
        def affected_person(eid, extra=()):
            affected = person_stores[str(eid)] | (set(extra) & all_stores)
            # Incomplete hours can change weekly overtime at another store.
            return affected or None
        known_checks = {'open_punches': [], 'zero_hour_closed_shifts': [],
                        'hours_only_ids': [], 'work_only_ids': []}
        if source:
            segments = {s['segment_id']: s for s in source['segments']}
            jobs = {str(j['jobCodeId']): j for j in report['datasets']['jobcodes']['records']}
            raw = {str(s['id']): s for s in report['datasets']['worksegments']['records']}
            for kind, category in [('open_punches', 'Open punch'), ('zero_hour_closed_shifts', 'Zero-hour shift')]:
                for segment in source['segments']:
                    if segment['category'] == category:
                        known_checks[kind].append(segment['segment_id'])
                        block(f'GEID {segment["employee_id"]}: {category}, {segment["time_in"]} '
                              f'(segment {segment["segment_id"]}).',
                              affected_person(segment['employee_id'], [segment['store']]))
            for kind in ('hours_only_ids', 'work_only_ids'):
                for sid in source['checks'][kind]:
                    s = segments.get(sid)
                    r = raw.get(sid)
                    known_checks[kind].append(sid)
                    eid = s['employee_id'] if s else (str(r['employeeId']) if r else '')
                    store = s['store'] if s else store_label(jobs.get(str((r or {}).get('jobCodeId')), {}).get('locationName'))
                    block(f'GEID {eid}: calculated/work segment mismatch {sid}.', affected_person(eid, [store]))
        # Only recognized issues with an exact employee/segment scope may be
        # isolated. Unknown/global issues continue to block every store pool.
        for issue in payroll.get('issues', []):
            if issue.startswith('GEID ') and ': ' in issue:
                eid = issue[5:].split(': ', 1)[0]
                block(issue, affected_person(eid))
            elif issue.startswith('TCP check requires correction: '):
                kind = issue.split(': ', 1)[1].split(' ', 1)[0]
                if not source or kind not in known_checks or not known_checks[kind]:
                    block(issue)
            else:
                block(issue)
        if not payroll.get('verified') and not payroll.get('issues'):
            block('TCP verification is incomplete without an actionable issue scope.')
        for exception in payroll.get('source_exceptions', []):
            block('Unverified source exception: ' + str(exception.get('reason', 'worked shift')),
                  affected_person(exception.get('employee_id', exception.get('geid', '')), [exception.get('store')]))
    by_store, global_roles = defaultdict(list), defaultdict(set)
    for row in rows:
        by_store[row['store']].append(row)
        global_roles[row['geid']].update(row['roles'])
        try:
            hours = [Decimal(row[f]) for f in ('regular_hours', 'overtime_hours', 'total_hours')]
            if any(not h.is_finite() or h < 0 for h in hours) or hours[0] + hours[1] != hours[2]:
                raise ValueError('Non-additive hours')
            unknown = set(row['roles']) - CREW - MANAGEMENT
            if unknown and not (set(row['roles']) == {'unassigned'} and hours[2] == 0):
                raise ValueError('Unknown role')
        except (TypeError, ValueError, ArithmeticError):
            block(f'GEID {row["geid"]}: incomplete hours or unclassified role.', {row['store']} if row['store'] in all_stores else None)
    pools = {p['store']: p for p in (tips or {}).get('stores', [])}
    if tips and (set(pools) != all_stores or len(pools) != len(tips.get('stores', []))):
        block('Gateway must return one verified tip pool for every configured store.')
    for sid in sorted(stores):
        detail = result['stores'][sid]
        detail['gateway_tips'] = pools.get(sid, {}).get('total_tips')
        detail['issues'] = list(dict.fromkeys(blocked[sid]))
        if detail['issues']:
            continue
        staff = [{**p, 'employee_id': p['geid'], 'roles': sorted(global_roles[p['geid']])} for p in by_store[sid]]
        try:
            # Validation above supplies a store-specific proof. The unchanged
            # exact-cent allocator still checks all rows, roles and pool sums.
            allocation = allocate(dict(period=tips['period'], verified=True, issues=[], stores=[pools[sid]]),
                                  dict(period=payroll['period'], verified=True, issues=[], employees=staff))
        except (ValueError, KeyError, TypeError) as exc:
            detail['issues'] = ['Store allocation failed validation: ' + str(exc)]
            continue
        amounts = {p['employee_id']: p['tips'] for p in allocation['employees']}
        for row in by_store[sid]:
            row['tips'] = amounts[row['geid']]
        detail.update(allocation['stores'][0], allocated=True)
    result['all_allocated'] = all(p['allocated'] for p in result['stores'].values())
    return result
