import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DOORDASH_STORES } from './disputes/doordash-stores.mjs';
import { lock, readJson, readLedger, writeJson, moneyCents, orderUrl, orderKey } from './state.mjs';

const source = process.argv[2];
if (!source) throw new Error('Usage: node import-history.mjs PATH_TO_OLD_FILED_DISPUTES_JSON');
const data = path.resolve(process.env.DOORDASH_DATA_DIR || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'data'));
const release = lock(data);
try {
  const old = readJson(source, null);
  if (!Array.isArray(old)) throw new Error('Expected an array in old ledger');
  const file = path.join(data, 'ledger.json');
  const ledger = readLedger(file);
  let added = 0;
  for (const row of old) {
    if (row.platform && row.platform !== 'DoorDash') continue;
    const store = DOORDASH_STORES.find(s => s.label === row.store);
    if (!store) throw new Error('Old record has an unknown store; no changes written');
    const href = orderUrl(row.href, store.storeId);
    const key = orderKey(href, store.storeId);
    const amountCents = moneyCents(row.amt);
    if (ledger.orders[key]) {
      if (ledger.orders[key].amountCents !== amountCents) throw new Error('History amount conflicts with ledger');
      continue;
    }
    ledger.orders[key] = {
      key, href, storeId: store.storeId, store: store.label, region: store.region,
      amountCents, reported: row.item || '', status: 'observed',
      filedAt: row.filedAt || null, importedAt: new Date().toISOString(),
    };
    added++;
  }
  writeJson(file, ledger);
  console.log(`Imported ${added} historical DoorDash orders. Their outcomes remain unverified until checked.`);
} finally { release(); }
