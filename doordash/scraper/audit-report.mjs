import ExcelJS from 'exceljs';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const label = value => value.replace(/^(CO|AZ|TX)-(\d+)$/, (_, region, n) => `${{ CO: '3356', AZ: '3759', TX: '3910' }[region]}-${n.padStart(2, '0')}`);
const money = '$#,##0.00;[Red]($#,##0.00)';

export function buildAuditReport(data) {
  const wb = new ExcelJS.Workbook();
  const total = wb.addWorksheet('Totals');
  total.addRows([
    ['DoorDash dispute audit'],
    ['Financial dates (inclusive)', `${data.start} to ${data.end}`],
    ['Orders checked', `${data.checked_orders} of ${data.expected_orders}`],
    ['Stores covered', data.stores.length],
    ['Confirmed recorded refund credits', data.totals.confirmed_credit_cents / 100],
    ['AB share on these credits (15%)', { formula: 'ROUND(B5*15%,2)', result: data.ab_share_on_confirmed_credits_cents / 100 }],
    ['Portal refunded orders', data.status_counts.approved || 0],
    ['Portal denied orders', data.status_counts.denied || 0],
    ['Portal pending orders', data.status_counts.pending || 0],
    ['Not submitted', data.status_counts.not_disputed || 0],
    ['Outcome unverified', data.status_counts.unknown || 0],
    ['Financial transactions reconciled', data.transaction_count],
    ['Review items', data.issues.length],
    ['Approved without credits in this period', data.orders.filter(o => o.status === 'approved' && !o.known_adjustment_cents).length],
    ['Scope', data.scope],
    ['Email / invoice', 'Review report only. No email sent; this is not an all-time amount owed across all platforms.'],
  ]);
  total.columns = [{ width: 42 }, { width: 100 }];
  total.getColumn(2).alignment = { wrapText: true, vertical: 'top' };
  total.getCell('B5').numFmt = money; total.getCell('B6').numFmt = money;
  total.getRow(15).height = 45; total.getRow(16).height = 32;
  const platforms = wb.addWorksheet('Platform coverage');
  platforms.addRows([
    ['Platform', 'Dates', 'Verified recorded credits ($)', 'Coverage'],
    ['DoorDash', `${data.start} to ${data.end}`, data.totals.confirmed_credit_cents / 100,
      `${data.checked_orders}/${data.expected_orders} orders; ${data.stores.length} stores. See review items.`],
    ['Uber Eats', '', null, 'Not audited: current dated refund records are not connected. Blank is not zero.'],
    ['Grubhub', '', null, 'Not audited: current dated refund records are not connected. Blank is not zero.'],
  ]);
  platforms.columns = [{ width: 20 }, { width: 30 }, { width: 28 }, { width: 88 }];
  platforms.getColumn(3).numFmt = money;
  const stores = wb.addWorksheet('By store');
  stores.addRow(['Store', 'Orders checked', 'Refunded', 'Denied', 'Pending', 'Not submitted', 'Confirmed credits ($)', 'Review items']);
  for (const s of data.stores) stores.addRow([label(s.label), s.orders, s.status_counts.approved || 0, s.status_counts.denied || 0,
    s.status_counts.pending || 0, s.status_counts.not_disputed || 0, s.confirmed_credit_cents / 100, s.issues]);
  stores.addRow(['TOTAL', data.checked_orders, data.status_counts.approved || 0, data.status_counts.denied || 0,
    data.status_counts.pending || 0, data.status_counts.not_disputed || 0, data.totals.confirmed_credit_cents / 100, data.issues.length]).font = { bold: true };
  stores.columns.forEach(c => { c.width = 22; }); stores.getColumn(7).numFmt = money;
  const orders = wb.addWorksheet('All orders');
  orders.addRow(['Store', 'Order', 'Outcome', 'Charges in period ($)', 'Known adjustments ($)', 'Confirmed credits ($)', 'Credit dates', 'Status checked (UTC)', 'Review / notes']);
  const transactions = wb.addWorksheet('Financial transactions');
  transactions.addRow(['Store', 'Order', 'Transaction ID', 'Local timestamp', 'Payout date', 'Type', 'Error charge ($)', 'Adjustment ($)']);
  for (const o of data.orders) {
    const href = `https://www.doordash.com/merchant/financials/transactions/${o.delivery_uuid}?store_id=${o.store_id}`;
    const creditDates = [...new Set(o.transactions.filter(t => t['Transaction type'] === 'Adjustment' && t.adjustment_cents > 0).map(t => t['Timestamp local time'].slice(0, 10)))].sort().join(', ');
    orders.addRow([label(o.store), { text: o.delivery_uuid.slice(-8).toUpperCase(), hyperlink: href },
      o.status === 'approved' ? 'Refunded' : o.status.replaceAll('_', ' '), o.charged_cents / 100, o.known_adjustment_cents / 100,
      o.confirmed_credit_cents === null ? null : o.confirmed_credit_cents / 100, creditDates, o.checked_at, [...o.issues, o.note].filter(Boolean).join('; ')]);
    for (const t of o.transactions) transactions.addRow([label(o.store), o.delivery_uuid.slice(-8).toUpperCase(), t['DoorDash transaction ID'],
      t['Timestamp local time'], t['Payout date'], t['Transaction type'], t.charge_cents === null ? null : t.charge_cents / 100,
      t.adjustment_cents === null ? null : t.adjustment_cents / 100]);
  }
  orders.columns.forEach(c => { c.width = 23; }); orders.getColumn(7).width = 32;
  orders.getColumn(8).width = 36; orders.getColumn(9).width = 80;
  for (const c of [4,5,6]) orders.getColumn(c).numFmt = money;
  transactions.columns.forEach(c => { c.width = 24; }); transactions.getColumn(3).width = 40;
  transactions.getColumn(4).width = 34; transactions.getColumn(7).numFmt = money; transactions.getColumn(8).numFmt = money;
  const issues = wb.addWorksheet('Review items');
  issues.addRow(['Store', 'Order', 'Needs attention']);
  for (const i of data.issues) issues.addRow([label(i.store), i.delivery_uuid.slice(-8).toUpperCase(), i.issue]);
  issues.columns = [{ width: 20 }, { width: 20 }, { width: 105 }];
  const sources = wb.addWorksheet('Sources');
  sources.addRow(['Source', 'Value']);
  for (const s of data.sources) { sources.addRow(['Official financial export', s.filename]); sources.addRow(['SHA256', s.sha256]); }
  sources.addRow(['Portal statuses', 'Read from the existing authenticated DoorDash session. No disputes were submitted.']);
  sources.addRow(['Method', 'Match immutable Store ID + Delivery UUID; count each financial transaction ID once; require refunded status for credits.']);
  sources.addRow(['Date basis', 'Credits use financial local posting dates. Payout dates are retained separately; current portal status may be later than the audit period.']);
  sources.columns = [{ width: 28 }, { width: 115 }];
  for (const sheet of wb.worksheets) {
    sheet.getRow(1).font = { bold: true, color: { argb: 'FFFFFFFF' } };
    sheet.getRow(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF243746' } };
    sheet.getRow(1).height = 34; sheet.getRow(1).alignment = { wrapText: true };
    sheet.views = [{ state: 'frozen', ySplit: 1 }];
    if (sheet !== total) {
      sheet.autoFilter = { from: { row: 1, column: 1 }, to: { row: sheet.rowCount, column: sheet.columnCount } };
      for (let i = 2; i <= sheet.rowCount; i++) { sheet.getRow(i).height = 32; sheet.getRow(i).alignment = { wrapText: true, vertical: 'top' }; }
    }
  }
  return wb;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const data = JSON.parse(fs.readFileSync(process.argv[2]));
  const target = process.argv[3]; fs.mkdirSync(path.dirname(target), { recursive: true });
  await buildAuditReport(data).xlsx.writeFile(target);
  console.log('Saved dated dispute audit workbook; no email sent.');
}
