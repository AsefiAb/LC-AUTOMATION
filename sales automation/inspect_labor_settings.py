"""Read Altametrics store labor settings without changing or saving them."""
import asyncio
import json
import re
from datetime import date
from pathlib import Path

import altametrics


def parse_settings(store, operations, laws, salary):
    def row(label, rows):
        found = [r for r in rows if r and r[0] == label]
        if len(found) != 1:
            raise ValueError('Missing or ambiguous setting: ' + label)
        return found[0][1:]
    starts, stops = row('Start of Day', operations), row('End of Day', operations)
    if len(starts) != 7 or len(stops) != 7 or len(set(starts)) != 1 or len(set(stops)) != 1:
        raise ValueError('Store has variable or incomplete business-day boundaries')
    start = re.fullmatch(r'(\d{2}):00 (AM|PM) - Today', starts[0])
    stop = re.fullmatch(r'(\d{2}):00 (AM|PM) - Tomorrow', stops[0])
    if not start or not stop or start.groups() != stop.groups():
        raise ValueError('Unsupported business-day boundary')
    cutoff = int(start[1]) % 12 + (12 if start[2] == 'PM' else 0)
    ot = [r[-1] for r in laws if len(r) == 3 and r[1] == 'OT Percentage']
    if len(ot) != 1 or len(salary) != 3:
        raise ValueError('Missing overtime or salaried/accrual settings')
    return {'store': store, 'cutoff_hour_local': cutoff,
            'overtime1_multiplier': str(altametrics.number(ot[0], True) / 100),
            'daily_salary': str(altametrics.number(salary[0])),
            'daily_salary_hours': salary[1],
            'daily_accrual': str(altametrics.number(salary[2])),
            'source': 'Altametrics Store Admin'}


async def table_rows(page):
    return await page.locator('tr:visible').evaluate_all(
        'es => es.map(e => [...e.cells].map(c => c.innerText.trim()))')


async def read_region_settings(page, region, day, config, set_stage=lambda _: None):
    output = []
    for store in (s['store'] for s in config['stores'] if s['region'] == region):
        set_stage('labor configuration for ' + store)
        await altametrics.select_store(page, store)
        await page.locator('a[aria-controls="Admin"]').click()
        await page.get_by_role('link', name='Organization Admin', exact=True).click()
        await page.get_by_role('link', name='Store Admin', exact=True).click()
        await altametrics.settle(page)
        franchise, index = store.split('-')
        full = f'0{franchise}-{int(index):05d}'
        await page.get_by_role('link', name=full, exact=True).click()
        await altametrics.settle(page)
        operations = await table_rows(page)
        await page.get_by_role('link', name='Settings', exact=True).click()
        await page.get_by_role('link', name='Labor Law/People Practice', exact=True).click()
        await altametrics.settle(page)
        laws = await table_rows(page)
        await page.get_by_role('link', name='Salaried and Accrual Settings', exact=True).click()
        await altametrics.settle(page)
        salary = await page.locator('input:visible').evaluate_all(
            'es => es.filter(e => e.type === "text").map(e => e.value)')
        result = parse_settings(store, operations, laws, salary)
        output.append(result)
        print(store + ': business-day and wage settings read; no changes made', flush=True)
        await page.locator('a[aria-controls="Home"]').click()
        await page.locator('a[href*="navToken=DASH_BOARD"]').click()
        await altametrics.settle(page)
    return output


if __name__ == '__main__':
    folder = Path(__file__).parent
    config = json.loads((folder / 'config.json').read_text())
    # Reuse the existing cloud login and isolated regional sessions, but inspect
    # settings instead of reading or writing sales reports.
    altametrics.read_region_report = read_region_settings
    result = asyncio.run(altametrics.collect(date.today().isoformat(), config))
    output = folder / 'reports' / 'altametrics-labor-settings.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f'Inspected {len(result)} stores. No emails or workbook changes.')
