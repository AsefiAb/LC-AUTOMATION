import { orderKey } from './state.mjs';

export async function collectStoreScans(stores, ledger, scan, checkpoint = async () => {}) {
  const review = { version: 1, generatedAt: new Date().toISOString(), complete: false,
    scope: 'Last 30 days; every error-charge transaction and every results page',
    stores: stores.map(s => ({ store: s.label, storeId: s.storeId, complete: false, attempted: false })), orders: [] };
  await checkpoint(review);
  for (let i = 0; i < stores.length; i++) {
    const coverage = review.stores[i]; coverage.attempted = true;
    try {
      const rows = await scan(stores[i]);
      if (!rows.coverage?.complete) throw new Error('Store scan did not verify complete pagination');
      Object.assign(coverage, rows.coverage, { foundOrders: rows.length });
      const newRows = rows.filter(row => !ledger.orders[orderKey(row.href, row.storeId)]);
      review.orders.push(...newRows);
      coverage.newOrders = newRows.length;
    } catch (error) {
      coverage.error = error.message;
      await checkpoint(review);
      // A global authentication/cooldown failure cannot be solved by hammering other stores.
      if (/Portal requires/.test(error.message)) break;
      continue;
    }
    await checkpoint(review);
  }
  review.complete = review.stores.every(s => s.complete);
  await checkpoint(review);
  return review;
}
