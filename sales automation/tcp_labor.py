"""Read-only TCP labor adapter. Persist aggregate store data, never employee rates."""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tips & payroll'))
from tcp_inspect import TCP, InspectionError


def decimal(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Missing or invalid numeric value.') from None
    if not result.is_finite() or result < 0:
        raise ValueError('Expected a finite nonnegative number.')
    return result


def duration(value):
    if isinstance(value, str) and ':' in value:
        match = re.fullmatch(r'(\d+):([0-5]\d)', value)
        if not match:
            raise ValueError('Invalid TCP duration.')
        return Decimal(match[1]) + Decimal(match[2]) / 60
    return decimal(value)


def money(value):
    return str(value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def collect(client, kind, params):
    output, seen = [], set()
    for page in range(1, 101):
        payload = client.request('https://api.tcplusondemand.com/v1/' + kind + '?' +
                                 urlencode({**params, 'perPage': 250, 'page': page}))
        if payload.get('errors') or not isinstance(payload.get('data'), list):
            raise InspectionError('Incomplete TCP ' + kind + ' response.')
        rows = payload['data']
        if not rows:
            return output
        fingerprint = json.dumps(rows, sort_keys=True)
        if fingerprint in seen:
            raise InspectionError('TCP repeated a page.')
        seen.add(fingerprint)
        output.extend(rows)
    raise InspectionError('TCP pagination limit reached.')


def business_date(value, cutoff_hour):
    stamp = datetime.fromisoformat(str(value))
    if stamp.tzinfo is not None:
        raise ValueError('TCP timestamp has an unexpected timezone offset.')
    return (stamp - timedelta(hours=cutoff_hour)).date().isoformat()


def summarize(report_date, hours, worksegments, jobs, expected_stores, *, cutoff_hour=0, settings=None):
    """Use all worked roles and TCP's calculated regular/OT split, never recompute payroll."""
    if type(cutoff_hour) is not int or not 0 <= cutoff_hour <= 23:
        raise ValueError('Invalid labor cutoff hour.')
    expected = set(expected_stores)
    if settings is not None and set(settings) != expected:
        raise ValueError('Altametrics costing settings do not cover every store.')
    issues = []
    totals = {store: {'regular': Decimal(0), 'ot1': Decimal(0), 'ot2': Decimal(0),
                      'base_wage_estimate': Decimal(0), 'wage_estimate': Decimal(0), 'segments': 0,
                      'rate_segments': 0} for store in expected}
    counts = Counter()
    selected_work = [s for s in worksegments if business_date(s.get('timeIn'), cutoff_hour) == report_date]
    work_ids = [str(s.get('id')) for s in selected_work]
    if len(work_ids) != len(set(work_ids)):
        issues.append('Duplicate TCP work segments.')
    seen = set()
    for person in hours:
        for segment in person.get('segments') or []:
            if business_date(segment.get('timeIn'), cutoff_hour) != report_date:
                continue
            sid = str(segment.get('recordId'))
            if sid == 'None' or sid in seen:
                issues.append('Duplicate or missing calculated segment ID.')
                continue
            seen.add(sid)
            job_ref = segment.get('jobCode') or {}
            job = jobs.get(str(job_ref.get('jobCodeId')), job_ref)
            if segment.get('isLeave') or job.get('leaveCode'):
                counts['leave_segments'] += 1
                continue
            location = str(job.get('locationName') or '')
            match = re.fullmatch(r'0*(\d+)-0*(\d+)', location)
            store = f'{int(match[1])}-{int(match[2]):02d}' if match else None
            if store not in expected:
                issues.append('Worked segment has an unmapped store.')
                continue
            target = totals[store]
            if not segment.get('timeOut') or segment.get('clockedInStatus'):
                issues.append(store + ': open punch.')
                counts['open_segments'] += 1
                continue
            try:
                vals = [duration((segment.get('hours') or {}).get(k))
                        for k in ('regular', 'overtime1', 'overtime2')]
            except ValueError:
                issues.append(store + ': missing calculated hours.')
                continue
            if not sum(vals) and segment['timeOut'] > segment['timeIn']:
                issues.append(store + ': closed shift has zero calculated hours.')
                counts['zero_hour_segments'] += 1
            for key, value in zip(('regular', 'ot1', 'ot2'), vals):
                target[key] += value
            target['segments'] += 1
            counts['worked_segments'] += 1
            try:
                rate = decimal(segment.get('rate'))
                if rate <= 0 and sum(vals) > 0:
                    raise ValueError('Zero rate with worked hours.')
                target['rate_segments'] += 1
                target['base_wage_estimate'] += sum(vals) * rate
                if settings is not None:
                    rules = settings[store]
                    cost = Decimal(money(vals[0] * rate))
                    for value, key in zip(vals[1:], ('overtime1_multiplier', 'overtime2_multiplier')):
                        if value:
                            multiplier = rules.get(key)
                            if multiplier is None or decimal(multiplier) < 1:
                                issues.append(store + ': overtime multiplier is not verified.')
                                continue
                            cost += Decimal(money(value * rate * decimal(multiplier)))
                    target['wage_estimate'] += cost
                counts['segments_with_positive_or_zero_valid_rate'] += 1
            except ValueError:
                counts['segments_without_usable_rate'] += 1
                issues.append(store + ': missing usable wage rate.')
    if seen != set(work_ids):
        issues.append('Calculated hours and work-segment IDs do not reconcile.')
    if not selected_work:
        issues.append('No work segments returned for the requested day.')
    stores = []
    for store in sorted(totals):
        t = totals[store]
        if not t['segments']:
            issues.append(store + ': no worked segments; zero labor is not assumed.')
        if settings is not None:
            # Nonzero salary/accrual needs reconciliation to TCP salaried records
            # before adding it, otherwise it could count the same wages twice.
            if decimal(settings[store]['daily_salary']) or decimal(settings[store]['daily_accrual']):
                issues.append(store + ': salary or accrual allocation needs reconciliation.')
        stores.append({'store': store, 'regular_hours': money(t['regular']),
                       'ot_hours': money(t['ot1'] + t['ot2']),
                       'total_hours': money(t['regular'] + t['ot1'] + t['ot2']),
                       'segments': t['segments'], 'rate_segments': t['rate_segments'],
                       'wage_estimate': money(t['wage_estimate']) if settings is not None else None,
                       'base_wage_estimate': money(t['base_wage_estimate'])
                       if t['segments'] and t['rate_segments'] == t['segments'] else None})
    global_issues = [issue for issue in issues
                     if not any(issue.startswith(store + ':') for store in expected)]
    for row in stores:
        row['issues'] = sorted(set(global_issues + [issue.split(': ', 1)[1]
            for issue in issues if issue.startswith(row['store'] + ':')]))
        row['complete'] = not row['issues']
        if not row['complete']:
            row['base_wage_estimate'] = None
            row['wage_estimate'] = None
    return {'report_date': report_date, 'source': 'TCP', 'complete': not issues,
            'issues': sorted(set(issues)), 'stores': stores, 'counts': dict(counts),
            'cost_model': 'altametrics_wage_formula' if settings is not None else 'base_wages',
            'cutoff_hour_local': cutoff_hour, 'scope': 'all_worked_roles',
            'labor_cost_verified': False,
            'cost_note': ('All worked roles, including management; regular wages plus configured OT wages. Uses TCP calculated hours/rates, which can differ from Altametrics punches and rounding. Salary/accrual and unknown OT rates block affected stores; taxes/benefits excluded.' if settings is not None else 'Base wage estimate only: hours times returned rate, without overtime premiums, salary allocation, taxes or benefits. Not verified actual labor cost.'),
            'date_basis': f'Whole shifts follow the local clock-in date after subtracting {cutoff_hour} hours. No worked hours are truncated.'}


def fetch(report_date, config):
    day = date.fromisoformat(report_date)
    settings = None
    cutoff = 0
    if config.get('labor_source') == 'TCP wage estimate':
        source = json.loads((Path(__file__).parent / config['labor_settings_file']).read_text())
        settings = {s['store']: s for s in source['stores']}
        if len(settings) != len(source['stores']):
            raise ValueError('Duplicate store labor settings.')
        cutoffs = {s['cutoff_hour_local'] for s in settings.values()}
        if len(cutoffs) != 1:
            raise ValueError('Different store business-day boundaries need separate queries.')
        cutoff = cutoffs.pop()
        if type(cutoff) is not int or not 0 <= cutoff <= 23:
            raise ValueError('Invalid labor cutoff hour.')
    start = datetime.combine(day, datetime.min.time()) + timedelta(hours=cutoff)
    stop = start + timedelta(days=1) - timedelta(seconds=1)
    client = TCP()
    client.authenticate()
    jobs = collect(client, 'jobcodes', {})
    hours = collect(client, 'hours', {'startDate': (day - timedelta(days=1)).isoformat(),
        'stopDate': (day + timedelta(days=2)).isoformat(), 'includeRate': 'true',
        'includeJobCode': 'true', 'includeOverlapping': 'false'})
    work = collect(client, 'worksegments', {'startDate': start.isoformat(),
        'stopDate': stop.isoformat(), 'includeSegmentTime': 'timeIn',
        'sortByWorkSegmentId': 'true'})
    return summarize(report_date, hours, work,
        {str(j['jobCodeId']): j for j in jobs}, [s['store'] for s in config['stores']],
        cutoff_hour=cutoff, settings=settings)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True)
    args = parser.parse_args()
    folder = Path(__file__).resolve().parent
    config = json.loads((folder / 'config.json').read_text())
    try:
        result = fetch(args.date, config)
        output = folder / 'reports' / ('tcp-labor-' + args.date + '.json')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'complete': result['complete'], 'counts': result['counts'],
                          'issues': result['issues']}))
    except (InspectionError, ValueError) as exc:
        print(str(exc))
        sys.exit(1)
