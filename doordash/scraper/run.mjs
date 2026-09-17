import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { DOORDASH_STORES } from './disputes/doordash-stores.mjs';
import { lock, readJson, readLedger, writeJson, orderKey, selectStores, validatePlan, submitOnce } from './state.mjs';
import { scanStore, prepare, navigate, readStatus } from './portal.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const data = path.resolve(process.env.DOORDASH_DATA_DIR || path.join(root, 'data'));
const { values } = parseArgs({ options: {
  mode: { type: 'string', default: 'scan' }, plan: { type: 'string' },
  stores: { type: 'string', default: '' }, limit: { type: 'string', default: '50' },
  help: { type: 'boolean', default: false },
} });
if (values.help) {
  console.log('node run.mjs --mode scan|submit|outcomes [--stores CO,AZ-1] [--plan FILE] [--limit 50]\nDefault: scan only. Submission requires an explicitly reviewed plan.');
} else {
  await main().catch(error => { console.error(error.message); process.exitCode = 1; });
}

async function main() {
  if (!['scan', 'submit', 'outcomes'].includes(values.mode)) throw new Error('Invalid mode');
  if (!/^\d+$/.test(values.limit) || +values.limit < 1 || +values.limit > 500) throw new Error('Limit must be 1–500');
  const stores = selectStores(DOORDASH_STORES, values.stores);
  const cdp = new URL(process.env.CDP_URL || 'http://127.0.0.1:9222');
  if (cdp.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(cdp.hostname) || cdp.username || cdp.password) {
    throw new Error('CDP_URL must point to Chrome on this computer');
  }
  const file = path.join(data, 'ledger.json');
  const release = lock(data);
  let browser, page;
  const run = { started: new Date().toISOString(), mode: values.mode, complete: false, results: [] };
  const report = path.join(root, 'reports', `run-${Date.now()}.json`);
  const checkpoint = () => writeJson(report, run);
  try {
    const ledger = readLedger(file);
    const plan = values.mode === 'submit' ? validatePlan(readJson(values.plan || '', {}), DOORDASH_STORES)
      .filter(row => stores.some(s => s.storeId === row.storeId)) : [];
    if (values.mode === 'submit' && !plan.length) throw new Error('No approved orders in the selected plan/stores');
    browser = await chromium.connectOverCDP(cdp.href, { timeout: 30000 });
    const context = browser.contexts()[0];
    if (!context) throw new Error('No Chrome session found');
    page = await context.newPage();
    page.setDefaultTimeout(15000);
    if (values.mode === 'scan') {
      const review = { version: 1, generatedAt: run.started, scope: 'Current portal date range; verify range and pagination in Chrome', orders: [] };
      for (const store of stores) {
        const rows = await scanStore(page, store);
        review.orders.push(...rows.filter(row => !ledger.orders[orderKey(row.href, row.storeId)]));
        run.results.push({ store: store.label, found: rows.length });
        writeJson(path.join(data, 'review-plan.json'), review);
        checkpoint();
        console.log(`${store.label}: ${rows.length} charge(s)`);
      }
      console.log(`Review plan: ${path.join(data, 'review-plan.json')}`);
    } else if (values.mode === 'submit') {
      const remaining = plan.filter(row => !ledger.orders[row.key]).slice(0, +values.limit);
      const fresh = new Map();
      // Reconcile every selected amount before any final submit.
      for (const store of stores.filter(s => remaining.some(r => r.storeId === s.storeId))) {
        for (const row of await scanStore(page, store)) fresh.set(orderKey(row.href, row.storeId), row);
      }
      for (const row of remaining) {
        if (fresh.get(row.key)?.amountCents !== row.amountCents) throw new Error(`Charge changed or not visible: ${row.store}; scan and review again`);
      }
      for (const row of remaining) {
        const form = await prepare(page, row);
        let result;
        if (form.status) {
          ledger.orders[row.key] = { ...row, status: form.status, observedAt: new Date().toISOString() };
          writeJson(file, ledger);
          result = 'already-' + form.status;
        } else result = await submitOnce({ ledger, file, row, ...form });
        run.results.push({ key: row.key, result });
        checkpoint();
        console.log(`${row.store}: ${result}`);
      }
      run.remaining = plan.filter(row => !ledger.orders[row.key]).length;
    } else {
      for (const [key, row] of Object.entries(ledger.orders)) {
        if (!stores.some(s => s.storeId === row.storeId)) continue;
        if (['approved', 'denied'].includes(row.status)) continue;
        await navigate(page, row.href);
        await page.waitForFunction(() => /dispute approved|dispute denied|dispute pending|under review|dispute charge/i.test(document.body.innerText), null, { timeout: 20000 });
        const status = await readStatus(page);
        row.lastCheckedAt = new Date().toISOString();
        row.lastObservedStatus = status;
        if (status !== 'unknown') row.status = status;
        writeJson(file, ledger);
        run.results.push({ key, status });
        checkpoint();
      }
    }
    run.complete = true;
  } catch (error) {
    run.error = error.message;
    if (page) {
      fs.mkdirSync(path.dirname(report), { recursive: true });
      await page.screenshot({ path: report.replace('.json', '.png') }).catch(() => {});
    }
    throw error;
  } finally {
    try { checkpoint(); }
    finally {
      if (page) await page.close().catch(() => {});
      // Playwright close on a CDP connection disconnects this client; it does not
      // close the user's browser. Only our created page is explicitly closed.
      if (browser) await browser.close().catch(() => {});
      release();
    }
  }
}
