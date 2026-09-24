import { moneyCents, orderKey, orderUrl } from './state.mjs';
import { financialsUrl } from './disputes/doordash-stores.mjs';

export async function guard(page) {
  // Allow an automatic browser check to finish; never treat its interim page
  // as an empty report, and never interact with a human verification prompt.
  if (/security verification/i.test(await page.locator('body').innerText({ timeout: 15000 }))) {
    await page.waitForFunction(() => !/security verification/i.test(document.body.innerText), null, { timeout: 30000 }).catch(() => {});
  }
  const text = await page.locator('body').innerText({ timeout: 15000 });
  if (/identity\.doordash|\/merchant\/login/.test(page.url()) ||
      /verify you are human|security verification|too many disputes|try again in 72|dispute limit/i.test(text)) {
    throw new Error('Portal requires login, verification, or a dispute cooldown; cloud access is not ready. No report was sent.');
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

export async function scanStore(page, store, { maxPages = 100, timeoutMs = 20000 } = {}) {
  await navigate(page, financialsUrl(store.storeId));
  await page.getByRole('button', { name: /^(today|yesterday|last \d+ days|this month|last month)$/i }).click();
  await page.getByRole('menuitem', { name: 'Last 30 days', exact: true }).click();
  await page.getByRole('button', { name: 'Last 30 days', exact: true }).waitFor({ state: 'visible' });
  await page.getByRole('button', { name: /transaction types/i }).click();
  const choices = page.getByRole('menuitemcheckbox');
  await page.getByRole('menuitemcheckbox', { name: /^error charge$/i }).waitFor({ state: 'visible' });
  for (let i = 0; i < await choices.count(); i++) {
    const option = choices.nth(i);
    const want = /^error charge$/i.test((await option.innerText()).trim());
    if ((await option.getAttribute('aria-checked') === 'true') !== want) await option.click();
  }
  await page.getByRole('button', { name: 'Apply', exact: true }).click();
  const ready = () => page.waitForFunction(() => {
    if (/no transactions|no results found|no matching transactions/i.test(document.body.innerText)) return true;
    const table = [...document.querySelectorAll('table,[role=table]')].find(t => /Error charges/i.test(t.innerText));
    if (!table) return false;
    const headers = [...table.querySelectorAll('th,[role=columnheader]')].map(e => e.innerText.trim());
    const type = headers.indexOf('Transaction type');
    const rows = [...table.querySelectorAll('tbody tr,[role=row]')].filter(r => r.querySelector('td,[role=cell]'));
    return type >= 0 && rows.length > 0 && rows.every(r => /^(Error charge)$/i.test(r.querySelectorAll('td,[role=cell]')[type]?.innerText.trim()));
  }, null, { timeout: timeoutMs });
  const snapshot = () => page.evaluate(() => {
    const text = document.body.innerText;
    const m = text.match(/([\d,]+)\s*[-–]\s*([\d,]+)\s+of\s+([\d,]+)\s+results/i);
    const range = m ? m.slice(1).map(v => Number(v.replaceAll(',', ''))) : null;
    const table = [...document.querySelectorAll('table,[role=table]')].find(t => /Error charges/i.test(t.innerText));
    const headers = table ? [...table.querySelectorAll('th,[role=columnheader]')].map(e => e.innerText.trim()) : [];
    const rows = table ? [...table.querySelectorAll('tbody tr,[role=row]')].filter(r => r.querySelector('td,[role=cell]')).map(tr => {
      const cells = [...tr.querySelectorAll('td,[role=cell]')];
      const field = name => cells[headers.indexOf(name)]?.innerText.trim();
      return { transactionId: field('Transaction ID'), type: field('Transaction type'), date: field('Date'),
        charge: field('Error charges'), href: tr.querySelector('a[href*="/merchant/financials/transactions/"]')?.getAttribute('href') };
    }) : [];
    return { range, rows, empty: /no transactions|no results found|no matching transactions/i.test(text) };
  });
  await ready();
  const orders = new Map(), transactions = new Set();
  let expected = null, cursor = 0;
  for (let index = 0; index < maxPages; index++) {
    await guard(page);
    const current = await snapshot();
    if (current.empty && current.rows.length === 0) {
      if (cursor || current.range?.[2]) throw new Error('Empty page before all transactions were read');
      const result = []; result.coverage = { complete: true, pages: 1, expectedTransactions: 0, readTransactions: 0, dateRange: 'Last 30 days' };
      return result;
    }
    if (!current.range) throw new Error('Portal result count missing; store completeness cannot be verified');
    const [first, last, total] = current.range;
    if (expected === null) expected = total;
    if (total !== expected || first !== cursor + 1 || last - first + 1 !== current.rows.length || last > total) {
      throw new Error('Pagination count changed or skipped rows; store scan is incomplete');
    }
    for (const row of current.rows) {
      if (!row.transactionId || !row.href || !/^error charge$/i.test(row.type || '') || !/^-\$[\d,]+(?:\.\d{2})?$/.test(row.charge || '')) {
        throw new Error('Error-charge columns missing or ambiguous; store scan is incomplete');
      }
      if (transactions.has(row.transactionId)) throw new Error('Pagination repeated a transaction; store scan is incomplete');
      transactions.add(row.transactionId);
      const href = orderUrl(row.href, store.storeId), key = orderKey(href, store.storeId);
      const charge = { transactionId: row.transactionId, date: row.date, amountCents: moneyCents(row.charge) };
      const existing = orders.get(key);
      if (existing) {
        existing.amountCents += charge.amountCents;
        existing.chargeTransactions.push(charge);
      } else orders.set(key, {
        storeId: store.storeId, store: store.label, region: store.region, href,
        amountCents: charge.amountCents, chargeTransactions: [charge], reported: 'Error charge; inspect the order and charged items',
        description: 'Please review this error charge and the supporting order, pickup, and delivery records. Please reverse any amount incorrectly attributed to the restaurant.',
        approved: false,
      });
    }
    cursor = last;
    const next = page.getByRole('button', { name: /^(next|next page|go to next page)$/i });
    const canNext = await next.count() && await next.first().isVisible() && !await next.first().isDisabled();
    if (cursor === expected) {
      if (canNext) throw new Error('Next page conflicts with reported transaction count');
      const result = [...orders.values()];
      result.coverage = { complete: true, pages: index + 1, expectedTransactions: expected, readTransactions: transactions.size, dateRange: 'Last 30 days' };
      return result;
    }
    if (!canNext) throw new Error('Next page unavailable before all transactions were read');
    await next.first().click();
    await page.waitForFunction(previous => {
      const m = document.body.innerText.match(/([\d,]+)\s*[-–]\s*([\d,]+)\s+of\s+([\d,]+)\s+results/i);
      return m && Number(m[1].replaceAll(',', '')) !== previous;
    }, first, { timeout: timeoutMs });
    await ready();
  }
  throw new Error('Pagination page limit reached; store scan is incomplete');
}

export async function readStatus(page) {
  const body = await guard(page);
  const index = body.lastIndexOf('Order:');
  const detail = index >= 0 ? body.slice(index) : body;
  const boundary = detail.indexOf('Delivery details');
  const text = boundary >= 0 ? detail.slice(0, boundary) : detail;
  // Conflicting labels are unresolved instead of choosing a favorable result.
  const found = [
    ['approved', /dispute approved/i], ['denied', /dispute denied/i],
    ['pending', /dispute pending|under review/i],
  ].filter(([, pattern]) => pattern.test(text));
  return found.length === 1 ? found[0][0] : 'unknown';
}

export function approvedRefundCents(text) {
  // Only an explicit refund label, not the order total or disputed amount.
  const matches = [
    ...text.matchAll(/(?:amount refunded|refund amount|refunded amount|receive a credit of)\s*[:\-]?\s*\$([\d,]+(?:\.\d{2})?)/gi),
    ...text.matchAll(/Error charge credit(?: \(includes tax\))?\s*\+\$([\d,]+(?:\.\d{2})?)/gi),
  ];
  const cents = [...new Set(matches.map(m => moneyCents(m[1])))];
  return cents.length === 1 ? cents[0] : null;
}

export async function readOutcome(page) {
  const status = await readStatus(page);
  const amount = status === 'approved' ? approvedRefundCents(await guard(page)) : null;
  return { status, approvedAmountCents: amount };
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
