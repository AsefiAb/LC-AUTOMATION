import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { lock, moneyCents, orderKey, orderUrl, readLedger, writeJson, selectStores, validatePlan, submitOnce, totals } from '../state.mjs';
import { DOORDASH_STORES } from '../disputes/doordash-stores.mjs';
import { buildReport } from '../report.mjs';

const href = 'https://www.doordash.com/merchant/financials/transactions/order-1?store_id=1150071';
const row = { href, storeId: '1150071', store: 'CO-1', region: 'CO', amountCents: 12345, description: 'Please review the supporting records for this error charge.', approved: true };
row.key = orderKey(href, row.storeId);
function temp(t) { const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dd-test-')); t.after(() => fs.rmSync(dir, { recursive: true, force: true })); return dir; }

test('currency parsing retains cents and rejects malformed or missing amounts', () => {
  assert.equal(moneyCents('-$1,234.56'), 123456);
  assert.equal(moneyCents('0.10'), 10);
  for (const value of ['', null, '$1,23', 'NaN', '1.234', '3abc']) assert.throws(() => moneyCents(value));
});
test('order keys ignore tracking parameters but isolate stores', () => {
  assert.equal(orderKey(href + '&x=y', row.storeId), row.key);
  assert.notEqual(orderKey(href, '1150117'), row.key);
  assert.equal(new URL(orderUrl(href, '1150117')).searchParams.get('store_id'), '1150117');
  for (const bad of ['https://evil.test/merchant/financials/transactions/1', '/merchant/financials/transactions', 'javascript:alert(1)']) assert.throws(() => orderUrl(bad, row.storeId));
});
test('unknown filters fail instead of silently scanning no stores', () => {
  assert.equal(selectStores(DOORDASH_STORES, 'CO,TX-1').length, 8);
  assert.throws(() => selectStores(DOORDASH_STORES, 'COO'));
});
test('exclusive lock prevents overlapping runs', t => {
  const dir = temp(t); const release = lock(dir);
  assert.throws(() => lock(dir), { code: 'EEXIST' });
  release(); lock(dir)();
});
test('corrupt ledger never silently becomes empty', t => {
  const file = path.join(temp(t), 'ledger.json');
  fs.writeFileSync(file, '{bad'); assert.throws(() => readLedger(file));
  writeJson(file, { version: 1, orders: { x: {} } }); assert.throws(() => readLedger(file));
});
test('only explicit approval is accepted and all approved rows are validated', () => {
  assert.equal(validatePlan({ version: 1, orders: [row, { approved: false }] }, DOORDASH_STORES).length, 1);
  assert.equal(validatePlan({ version: 1, orders: [{ ...row, approved: 'true' }] }, DOORDASH_STORES).length, 0);
  assert.throws(() => validatePlan({ version: 1, orders: [row, row] }, DOORDASH_STORES));
  assert.throws(() => validatePlan({ version: 1, orders: [{ ...row, store: 'TX-1' }] }, DOORDASH_STORES));
  assert.throws(() => validatePlan({ version: 1, orders: [{ ...row, amountCents: NaN }] }, DOORDASH_STORES));
});
test('attempt is durable before click and confirmed submission is never repeated', async t => {
  const file = path.join(temp(t), 'ledger.json'); const ledger = readLedger(file); let clicks = 0;
  const submit = async () => { assert.equal(readLedger(file).orders[row.key].status, 'submitting'); clicks++; };
  assert.equal(await submitOnce({ file, ledger, row, submit, status: async () => 'pending' }), 'pending');
  assert.equal(await submitOnce({ file, ledger: readLedger(file), row, submit, status: async () => 'pending' }), 'already-recorded');
  assert.equal(clicks, 1);
});
test('timeout after submit is uncertain and cannot be submitted again', async t => {
  const file = path.join(temp(t), 'ledger.json'); const ledger = readLedger(file); let clicks = 0;
  await assert.rejects(submitOnce({ file, ledger, row, submit: async () => { clicks++; throw new Error('timeout'); }, status: async () => 'pending' }));
  assert.equal(readLedger(file).orders[row.key].status, 'uncertain');
  await submitOnce({ file, ledger: readLedger(file), row, submit: async () => clicks++, status: async () => 'pending' });
  assert.equal(clicks, 1);
});
test('unknown confirmation is not counted as pending or approved', async t => {
  const file = path.join(temp(t), 'ledger.json'); const ledger = readLedger(file);
  await assert.rejects(submitOnce({ file, ledger, row, submit: async () => {}, status: async () => 'unknown' }));
  assert.equal(totals(Object.values(ledger.orders)).uncertain, 12345);
  assert.equal(totals(Object.values(ledger.orders)).approved, 0);
});
test('XLSX totals distinguish approved from pending and unverified', async () => {
  const ledger = { orders: { a: { ...row, status: 'approved' }, b: { ...row, status: 'pending', amountCents: 10 }, c: { ...row, status: 'observed', amountCents: 20 } } };
  const wb = buildReport(ledger);
  assert.equal(wb.getWorksheet('By store').getCell('C2').value, 123.45);
  assert.equal(wb.getWorksheet('By store').getCell('E2').value, 0.10);
  assert.equal(wb.getWorksheet('By store').getCell('G2').value, 0.20);
  assert.ok((await wb.xlsx.writeBuffer()).byteLength > 1000);
});


test('savings workbook separates partial refunds from full disputed charges and shows AB share', () => {
  const ledger = { version: 1, orders: { [row.key]: { ...row, status: 'approved', approvedAmountCents: 4000 } } };
  const summary = { first_email: true, period_start: '2026-09-08', period_end: '2026-09-21', newly_verified_savings_cents: 4000,
    ab_cut_cents: 600, first_unpaid_ab_cut_cents: 600, cumulative_savings_cents: 4000, issues: [], basis: 'Verified refunds' };
  const wb = buildReport(ledger, summary, { orders: [] });
  assert.equal(wb.getWorksheet('Dispute savings').getCell('B2').value, 40);
  assert.equal(wb.getWorksheet('Dispute savings').getCell('B3').value, 6);
  assert.equal(wb.getWorksheet('By store').getCell('H3').value, 40);
  assert.equal(wb.getWorksheet('Orders').getCell('H2').value, 40);
  assert.ok(wb.getWorksheet('Evidence review'));
  assert.ok(wb.getWorksheet('Issues'));
});

test('multi-platform snapshot is retained separately from overlapping live DoorDash orders', () => {
  const metric = recovered_cents => ({ recovered_cents, approved_count: 1, denied_count: 0, pending_count: 0 });
  const snapshot = { source_name: 'DisputeTracker.xlsx', generated_at: '2026-09-21',
    platforms: { DoorDash: metric(1000), 'Uber Eats': metric(2000), Grubhub: metric(0) },
    reported_recovered_cents: 3000, illustrative_ab_cut_cents: 450, issues: ['Historical snapshot only'],
    stores: [{ platform: 'Uber Eats', account: 'Uber Eats account 2', store: 'Store 2', ...metric(2000) }] };
  const wb = buildReport({ version: 1, orders: { [row.key]: { ...row, status: 'approved', approvedAmountCents: 4000 } } }, null, null, snapshot);
  assert.equal(wb.getWorksheet('All platform totals').getCell('B5').value, 10);
  assert.equal(wb.getWorksheet('All platform totals').getCell('B8').value.result, 30);
  assert.equal(wb.getWorksheet('Historical platform stores').rowCount, 2);
  assert.equal(wb.getWorksheet('By store').getCell('H2').value, 40);
});
