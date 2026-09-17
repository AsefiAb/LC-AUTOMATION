import ExcelJS from 'exceljs';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readLedger, totals } from './state.mjs';

export function buildReport(ledger) {
  const wb = new ExcelJS.Workbook();
  const summary = wb.addWorksheet('By store');
  summary.addRow(['Store', 'Orders tracked', 'Approved ($)', 'Denied ($)', 'Pending ($)', 'Uncertain ($)', 'Not yet verified ($)']);
  const entries = Object.values(ledger.orders);
  for (const store of [...new Set(entries.map(r => r.store))].sort()) {
    const rows = entries.filter(r => r.store === store);
    const t = totals(rows);
    summary.addRow([store, rows.length, t.approved / 100, t.denied / 100, t.pending / 100, t.uncertain / 100, t.observed / 100]);
  }
  const t = totals(entries);
  summary.addRow(['TOTAL', entries.length, t.approved / 100, t.denied / 100, t.pending / 100, t.uncertain / 100, t.observed / 100]).font = { bold: true };
  summary.addRow([]);
  summary.addRow(['Cumulative tracked orders. Approval is the portal status; bank/payout reconciliation is separate.']);
  summary.addRow(['Status last checked per order appears on the Orders sheet. Unknown reads preserve the last confirmed status.']);
  const detail = wb.addWorksheet('Orders');
  detail.addRow(['Store', 'Order URL', 'Amount ($)', 'Last confirmed status', 'Filed at', 'Last checked at', 'Latest observation']);
  for (const row of entries) detail.addRow([row.store, row.href, row.amountCents / 100, row.status, row.filedAt || '', row.lastCheckedAt || '', row.lastObservedStatus || '']);
  for (const sheet of [summary, detail]) {
    sheet.getRow(1).font = { bold: true, color: { argb: 'FFFFFFFF' } };
    sheet.getRow(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF243746' } };
    sheet.views = [{ state: 'frozen', ySplit: 1 }];
    sheet.columns.forEach(c => { c.width = 23; });
  }
  detail.getColumn(2).width = 75;
  for (let column = 3; column <= 7; column++) summary.getColumn(column).numFmt = '$#,##0.00';
  detail.getColumn(3).numFmt = '$#,##0.00';
  return wb;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const data = path.resolve(process.env.DOORDASH_DATA_DIR || path.join(root, 'data'));
  const ledger = readLedger(path.join(data, 'ledger.json'));
  const out = path.join(root, 'reports', `DoorDash-Tracker-${new Date().toISOString().replace(/[:.]/g, '-')}.xlsx`);
  fs.mkdirSync(path.dirname(out), { recursive: true });
  await buildReport(ledger).xlsx.writeFile(out);
  console.log(`Saved ${out}`);
}
