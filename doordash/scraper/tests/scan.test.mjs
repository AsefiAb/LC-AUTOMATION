import test from 'node:test';
import assert from 'node:assert/strict';
import { collectStoreScans } from '../scan.mjs';
import { DOORDASH_STORES } from '../disputes/doordash-stores.mjs';

function result(store) {
  const rows = [1,2,3].map(n => ({ storeId: store.storeId, store: store.label, amountCents: n * 100,
    href: `https://www.doordash.com/merchant/financials/transactions/order-${n}?store_id=${store.storeId}` }));
  rows.coverage = { complete: true, pages: 2, expectedTransactions: 3, readTransactions: 3 };
  return rows;
}

test('all 18 stores retain every dispute, not the first matching order or store', async () => {
  const visited = [];
  const plan = await collectStoreScans(DOORDASH_STORES, { orders: {} }, async store => {
    visited.push(store.storeId); return result(store);
  });
  assert.equal(visited.length, 18);
  assert.equal(plan.orders.length, 54);
  assert.equal(plan.stores.length, 18);
  assert.equal(plan.complete, true);
  assert.ok(plan.stores.every(s => s.foundOrders === 3 && s.complete));
});

test('one failed store does not hide later stores or masquerade as zero disputes', async () => {
  const plan = await collectStoreScans(DOORDASH_STORES, { orders: {} }, async store => {
    if (store.label === 'CO-2') throw new Error('Pagination count mismatch');
    return result(store);
  });
  assert.equal(plan.complete, false);
  assert.equal(plan.orders.length, 51);
  assert.equal(plan.stores.filter(s => s.attempted).length, 18);
  assert.match(plan.stores[1].error, /mismatch/);
  assert.equal(plan.stores[1].foundOrders, undefined);
  assert.equal(plan.stores.at(-1).complete, true);
});

test('global verification block leaves remaining stores explicitly unattempted', async () => {
  const plan = await collectStoreScans(DOORDASH_STORES, { orders: {} }, async () => { throw new Error('Portal requires login'); });
  assert.equal(plan.complete, false);
  assert.equal(plan.stores.filter(s => s.attempted).length, 1);
  assert.ok(plan.stores.slice(1).every(s => !s.complete && !s.attempted));
});
