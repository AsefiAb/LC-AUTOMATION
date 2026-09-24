import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

export function readJson(file, fallback) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return fallback; throw error; }
}

export function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.${crypto.randomUUID()}.tmp`;
  try {
    fs.writeFileSync(tmp, JSON.stringify(value, null, 2), { flag: 'wx' });
    fs.renameSync(tmp, file);
  } finally { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); }
}

export function lock(folder) {
  fs.mkdirSync(folder, { recursive: true });
  const file = path.join(folder, 'run.lock');
  const fd = fs.openSync(file, 'wx');
  fs.writeFileSync(fd, JSON.stringify({ pid: process.pid, started: new Date().toISOString() }));
  fs.closeSync(fd);
  return () => fs.unlinkSync(file);
}

export function moneyCents(value) {
  const raw = String(value ?? '').trim();
  if (!/^-?\$?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?$/.test(raw)) {
    throw new Error(`Invalid monetary amount: ${raw}`);
  }
  const [whole, fraction = ''] = raw.replace(/[-$,]/g, '').split('.');
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (!Number.isSafeInteger(cents)) throw new Error('Amount exceeds supported range');
  return cents;
}

export function orderUrl(href, storeId) {
  const url = new URL(href, 'https://www.doordash.com');
  if (url.origin !== 'https://www.doordash.com' || url.username || url.password ||
      !/^\/merchant\/financials\/transactions\/[^/]+\/?$/.test(url.pathname)) {
    throw new Error('Unexpected DoorDash transaction URL');
  }
  if (!/^\d+$/.test(String(storeId))) throw new Error('Invalid store ID');
  url.search = '';
  url.hash = '';
  url.searchParams.set('store_id', storeId);
  return url.href;
}

export function orderKey(href, storeId) {
  return `${storeId}:${new URL(orderUrl(href, storeId)).pathname.replace(/\/$/, '')}`;
}

export function selectStores(stores, filter = '') {
  if (!filter.trim()) return stores;
  const tokens = filter.split(',').map(s => s.trim().toUpperCase());
  for (const token of tokens) {
    if (!stores.some(s => [s.region, s.label, s.storeId].includes(token))) {
      throw new Error(`Unknown store filter: ${token}`);
    }
  }
  return stores.filter(s => tokens.some(t => [s.region, s.label, s.storeId].includes(t)));
}

export function readLedger(file) {
  const value = readJson(file, { version: 1, orders: {} });
  if (value.version !== 1 || !value.orders || Array.isArray(value.orders) || typeof value.orders !== 'object') {
    throw new Error('Invalid ledger; restore its backup before continuing');
  }
  for (const [key, entry] of Object.entries(value.orders)) {
    if (key !== orderKey(entry.href, entry.storeId) || !Number.isSafeInteger(entry.amountCents) || entry.amountCents < 0 ||
        !['submitting', 'uncertain', 'pending', 'approved', 'denied', 'observed'].includes(entry.status)) {
      throw new Error(`Invalid ledger entry: ${key}`);
    }
  }
  return value;
}

export function validatePlan(plan, stores) {
  if (plan.version !== 1 || plan.complete !== true || !Array.isArray(plan.orders)) throw new Error('Invalid or incomplete review plan');
  const seen = new Set();
  return plan.orders.filter(row => row.approved === true).map(row => {
    const store = stores.find(s => s.storeId === row.storeId);
    if (!store || row.store !== store.label) throw new Error('Plan store does not match configured store');
    const href = orderUrl(row.href, row.storeId);
    const key = orderKey(href, row.storeId);
    if (seen.has(key)) throw new Error('Duplicate approved order in plan');
    seen.add(key);
    if (!Number.isSafeInteger(row.amountCents) || row.amountCents <= 0) throw new Error('Invalid plan amount');
    if (typeof row.description !== 'string' || row.description.trim().length < 20 || row.description.length > 2000) {
      throw new Error('Each approved order needs a reviewed description (20–2000 characters)');
    }
    return { ...row, href, key };
  });
}

// Persist before the only side effect. Never retry an ambiguous final click.
export async function submitOnce({ ledger, file, row, submit, status }) {
  if (ledger.orders[row.key]) return 'already-recorded';
  ledger.orders[row.key] = { ...row, status: 'submitting', attemptedAt: new Date().toISOString() };
  writeJson(file, ledger);
  try {
    await submit();
    const outcome = await status();
    if (!['pending', 'approved', 'denied'].includes(outcome)) throw new Error('Submission confirmation missing');
    Object.assign(ledger.orders[row.key], { status: outcome, filedAt: new Date().toISOString() });
    writeJson(file, ledger);
    return outcome;
  } catch (error) {
    ledger.orders[row.key].status = 'uncertain';
    writeJson(file, ledger);
    throw error;
  }
}

export function totals(entries) {
  const result = { approved: 0, denied: 0, pending: 0, uncertain: 0, observed: 0 };
  for (const row of entries) {
    const status = row.status === 'submitting' ? 'uncertain' : row.status;
    if (!(status in result)) throw new Error('Unrecognized outcome');
    result[status] += row.amountCents;
  }
  return result;
}
