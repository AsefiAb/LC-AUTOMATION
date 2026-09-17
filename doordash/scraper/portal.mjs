import { moneyCents, orderKey, orderUrl } from './state.mjs';
import { financialsUrl } from './disputes/doordash-stores.mjs';

export async function guard(page) {
  const text = await page.locator('body').innerText({ timeout: 15000 });
  if (/identity\.doordash|\/merchant\/login/.test(page.url()) ||
      /verify you are human|security verification|too many disputes|try again in 72|dispute limit/i.test(text)) {
    throw new Error('Portal requires login, verification, or a dispute cooldown; finish in Chrome before retrying');
  }
  return text;
}

export async function navigate(page, url) {
  // Only navigation is retried. Submission is never retried.
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await guard(page);
      return;
    } catch (error) {
      if (/Portal requires/.test(error.message) || attempt === 2) throw error;
      await page.waitForTimeout(500 * 2 ** attempt);
    }
  }
}

async function visibleRows(page, store) {
  const rows = await page.locator('a[href*="/merchant/financials/transactions/"]').evaluateAll(links => {
    return links.map(a => ({ href: a.getAttribute('href'), text: (a.closest('tr') || a.closest('[role=row]') || a).innerText.replace(/\s+/g, ' ').trim() }))
      .filter(row => /error charge|adjustment/i.test(row.text));
  });
  return rows.map(row => {
    const amounts = row.text.match(/-\$[\d,]+(?:\.\d{2})?/g);
    if (!amounts?.length) throw new Error('Charge amount missing; portal layout needs review');
    return {
      storeId: store.storeId, store: store.label, region: store.region,
      href: orderUrl(row.href, store.storeId),
      amountCents: moneyCents(amounts.at(-1)),
      reported: row.text,
      description: 'Please review this error charge and the supporting order, pickup, and delivery records. Please reverse any amount incorrectly attributed to the restaurant.',
      approved: false,
    };
  });
}

export async function scanStore(page, store) {
  await navigate(page, financialsUrl(store.storeId));
  await page.getByRole('button', { name: /transaction types/i }).click();
  for (const label of [/error charge/i, /adjustment/i]) {
    const option = page.getByRole('menuitemcheckbox', { name: label });
    await option.waitFor({ state: 'visible' });
    if (await option.getAttribute('aria-checked') !== 'true') await option.click();
  }
  await page.getByRole('button', { name: 'Apply', exact: true }).click();
  const ready = () => page.waitForFunction(() => {
    const rows = [...document.querySelectorAll('a[href*="/merchant/financials/transactions/"]')];
    return rows.some(a => /error charge|adjustment/i.test((a.closest('tr') || a.closest('[role=row]') || a).innerText)) ||
      /no transactions|no results found|no matching transactions/i.test(document.body.innerText);
  }, null, { timeout: 20000 });
  await ready();
  const orders = new Map();
  const signatures = new Set();
  for (let index = 0; index < 100; index++) {
    await guard(page);
    const rows = await visibleRows(page, store);
    const signature = rows.map(r => r.href).join('|');
    if (signatures.has(signature)) throw new Error('Pagination repeated a page; store scan is incomplete');
    signatures.add(signature);
    for (const row of rows) {
      const key = orderKey(row.href, store.storeId);
      if (orders.has(key) && orders.get(key).amountCents !== row.amountCents) {
        throw new Error('Multiple charge amounts for one order; inspect the charge manually');
      }
      orders.set(key, row);
    }
    const next = page.getByRole('button', { name: /^(next|next page|go to next page)$/i });
    if (!(await next.count()) || !(await next.first().isVisible()) || await next.first().isDisabled()) {
      return [...orders.values()];
    }
    const previous = await page.locator('a[href*="/merchant/financials/transactions/"]').evaluateAll(a => a.map(x => x.getAttribute('href')).join('|'));
    await next.first().click();
    await page.waitForFunction(old => [...document.querySelectorAll('a[href*="/merchant/financials/transactions/"]')].map(a => a.getAttribute('href')).join('|') !== old, previous, { timeout: 20000 });
    await ready();
  }
  throw new Error('Pagination exceeded 100 pages; narrow the portal date range');
}

export async function readStatus(page) {
  const text = await guard(page);
  // Conflicting labels are unresolved instead of choosing a favorable result.
  const found = [
    ['approved', /dispute approved/i], ['denied', /dispute denied/i],
    ['pending', /dispute pending|under review/i],
  ].filter(([, pattern]) => pattern.test(text));
  return found.length === 1 ? found[0][0] : 'unknown';
}

export async function prepare(page, row) {
  await navigate(page, row.href);
  const button = page.getByRole('button', { name: 'Dispute charge', exact: true });
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Dispute charge') ||
    /dispute approved|dispute denied|dispute pending|under review/i.test(document.body.innerText), null, { timeout: 20000 });
  const prior = await readStatus(page);
  if (prior !== 'unknown') return { status: prior };
  await button.click();
  const dialog = page.getByRole('dialog').filter({ has: page.getByRole('button', { name: 'Submit for review', exact: true }) });
  await dialog.waitFor({ state: 'visible', timeout: 15000 });
  // Restrict all edits to the dispute dialog, never page-wide checkboxes.
  const boxes = dialog.getByRole('checkbox');
  for (let i = 0; i < await boxes.count(); i++) await boxes.nth(i).setChecked(true);
  for (let i = 0; i < 30; i++) {
    const choices = dialog.getByRole('button', { name: 'Choose an option', exact: true });
    if (!(await choices.count())) break;
    await choices.first().click();
    const reason = page.getByRole('menuitem', { name: 'Other reason', exact: true })
      .or(page.getByRole('option', { name: 'Other reason', exact: true }));
    await reason.first().click();
  }
  const fields = dialog.locator('textarea');
  if (!(await fields.count())) throw new Error('No description fields found');
  for (let i = 0; i < await fields.count(); i++) await fields.nth(i).fill(row.description);
  if (await dialog.getByRole('button', { name: 'Choose an option', exact: true }).count()) throw new Error('An item reason is incomplete');
  const submit = dialog.getByRole('button', { name: 'Submit for review', exact: true });
  if (!await submit.isEnabled()) throw new Error('Dispute form is incomplete');
  return {
    submit: () => submit.click({ timeout: 15000 }),
    status: async () => {
      await page.waitForFunction(() => /dispute approved|dispute denied|dispute pending|under review/i.test(document.body.innerText), null, { timeout: 15000 });
      return readStatus(page);
    },
  };
}
