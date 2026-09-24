import ExcelJS from 'exceljs';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readLedger, totals } from './state.mjs';

export function buildReport(ledger, abSummary = null, reviewPlan = null, trackerSnapshot = null, financial = null) {
  const wb = new ExcelJS.Workbook();
  if (financial) {
    if (financial.billing_ready !== false) throw new Error('Financial export review cannot be an AB invoice');
    const sheet = wb.addWorksheet('Financial export review');
    sheet.addRow(['DoorDash financial export review']);
    sheet.addRow(['NOT AN INVOICE', 'Linked adjustments require confirmation as dispute recoveries. No AB cut is billed here.']);
    sheet.addRow(['Reconciled at', financial.generated_at]);
    sheet.addRow(['Tracked orders', financial.tracked_orders]);
    sheet.addRow(['Matched orders', financial.matched_orders]);
    sheet.addRow(['Orders with adjustments', financial.orders_with_adjustments]);
    sheet.addRow(['Known net adjustments ($)', financial.known_net_adjustment_cents / 100]);
    sheet.addRow(['Other store IDs excluded', financial.other_store_ids.join(', ')]);
    sheet.addRow(['Basis', financial.basis]);
    for (const source of financial.sources) sheet.addRow(['Source file', source.filename]);
    sheet.columns = [{ width: 34 }, { width: 95 }];
    sheet.getColumn(2).alignment = { wrapText: true, vertical: 'top' };
    sheet.getRow(2).font = { bold: true, color: { argb: 'FF9C0006' } };
    sheet.getRow(2).height = 34;
    sheet.getRow(9).height = 34;
    sheet.getCell('B7').numFmt = '$#,##0.00;[Red]($#,##0.00)';
    const orders = wb.addWorksheet('Financial order review');
    orders.addRow(['Store', 'Delivery ID', 'Ledger status', 'Matched', 'Adjustment count',
      'Known net adjustments ($)', 'Amounts complete', 'Review needed']);
    const transactions = wb.addWorksheet('Financial transactions');
    transactions.addRow(['Store', 'Delivery ID', 'Transaction ID', 'Local timestamp', 'Payout date',
      'Type', 'Description', 'Error charge ($)', 'Adjustment ($)']);
    for (const order of financial.orders) {
      orders.addRow([order.store, order.delivery_uuid, order.ledger_status, order.matched ? 'Yes' : 'No',
        order.adjustment_count, order.known_net_adjustment_cents / 100,
        order.adjustment_amount_complete ? 'Yes' : 'No', order.issues.join('; ')]);
      for (const tx of order.transactions) transactions.addRow([order.store, order.delivery_uuid,
        tx['DoorDash transaction ID'], tx['Timestamp local time'], tx['Payout date'], tx['Transaction type'],
        tx.Description, tx.charge_cents === null ? null : tx.charge_cents / 100,
        tx.adjustment_cents === null ? null : tx.adjustment_cents / 100]);
    }
    for (const tab of [orders, transactions]) {
      tab.views = [{ state: 'frozen', ySplit: 1 }];
      tab.getRow(1).font = { bold: true, color: { argb: 'FFFFFFFF' } };
      tab.getRow(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF243746' } };
      tab.getRow(1).alignment = { wrapText: true }; tab.getRow(1).height = 34;
      tab.columns.forEach(c => { c.width = 23; });
      tab.autoFilter = { from: { row: 1, column: 1 }, to: { row: tab.rowCount, column: tab.columnCount } };
    }
    orders.getColumn(2).width = 40; orders.getColumn(8).width = 90;
    orders.getColumn(8).alignment = { wrapText: true };
    orders.getColumn(6).numFmt = '$#,##0.00;[Red]($#,##0.00)';
    transactions.getColumn(2).width = 40; transactions.getColumn(3).width = 40;
    transactions.getColumn(8).numFmt = '$#,##0.00;[Red]($#,##0.00)';
    transactions.getColumn(9).numFmt = '$#,##0.00;[Red]($#,##0.00)';
  }
  if (trackerSnapshot) {
    const all = wb.addWorksheet('All platform totals');
    all.addRow(['Reported platform totals']);
    all.addRow(['Historical source', trackerSnapshot.source_name, trackerSnapshot.generated_at]);
    all.addRow(['Historical figures are not added to live DoorDash totals. Fresh multi-platform refunds are still required.']);
    all.addRow(['Platform', 'Reported recovered ($)', 'Approved (#)', 'Denied (#)', 'Pending (#)', 'AB share example ($)']);
    for (const [platform, value] of Object.entries(trackerSnapshot.platforms)) {
      const i = all.rowCount + 1;
      all.addRow([platform, value.recovered_cents / 100, value.approved_count, value.denied_count, value.pending_count,
        { formula: `ROUND(B${i}*15%,2)`, result: Math.round(value.recovered_cents * .15) / 100 }]);
    }
    const last = all.rowCount;
    all.addRow(['TOTAL', { formula: `SUM(B5:B${last})`, result: trackerSnapshot.reported_recovered_cents / 100 },
      ...['C','D','E'].map(c => ({ formula: `SUM(${c}5:${c}${last})` })),
      { formula: `ROUND(B${last+1}*15%,2)`, result: trackerSnapshot.illustrative_ab_cut_cents / 100 }]);
    all.addRow(['The 15% illustration is not a verified invoice. Partial refunds and missing account coverage must be reconciled.']);
    for (const issue of trackerSnapshot.issues) all.addRow([issue]);
    all.columns.forEach(c => { c.width = 24; }); all.getColumn(1).width = 60;
    all.getColumn(2).numFmt = '$#,##0.00'; all.getColumn(6).numFmt = '$#,##0.00';
    all.getRow(4).font = { bold: true }; all.getRow(4).alignment = { wrapText: true }; all.getRow(4).height = 32;
    const sourceStores = wb.addWorksheet('Historical platform stores');
    sourceStores.addRow(['Platform', 'Account', 'Store', 'Recovered ($)', 'Approved (#)', 'Denied (#)', 'Pending (#)', 'Source timestamp']);
    for (const r of trackerSnapshot.stores) sourceStores.addRow([r.platform, r.account, r.store, r.recovered_cents / 100, r.approved_count, r.denied_count, r.pending_count, trackerSnapshot.generated_at]);
    sourceStores.columns.forEach(c => { c.width = 24; }); sourceStores.getColumn(3).width = 55;
    sourceStores.getColumn(4).numFmt = '$#,##0.00'; sourceStores.getRow(1).font = { bold: true };
  }
  if (abSummary) {
    const sheet = wb.addWorksheet('Dispute savings');
    sheet.addRow(['DoorDash dispute savings', `${abSummary.period_start} to ${abSummary.period_end}`]);
    sheet.addRow([abSummary.first_email ? 'All-time verified savings' : 'Newly verified savings', abSummary.newly_verified_savings_cents / 100]);
    sheet.addRow(['AB Cut (15%)', abSummary.ab_cut_cents / 100]);
    if (abSummary.first_email) {
      sheet.addRow(['Previously paid to AB', 0]);
      sheet.addRow(['Total still owed to AB', abSummary.first_unpaid_ab_cut_cents / 100]);
    }
    sheet.addRow(['Cumulative verified refunds', abSummary.cumulative_savings_cents / 100]);
    sheet.addRow(['Calculation', 'Verified refunds × 15%; rounded to cents. Previously reported savings are not billed again.']);
    sheet.addRow(['Basis', abSummary.basis]);
    sheet.getColumn(1).width = 34; sheet.getColumn(2).width = 95;
    sheet.getColumn(2).numFmt = '$#,##0.00';
    sheet.getColumn(2).alignment = { wrapText: true };
    const issues = wb.addWorksheet('Issues');
    issues.addRow(['Store', 'Order', 'Needs review']);
    for (const row of abSummary.issues) issues.addRow([row.store, row.key, row.reason]);
    if (!abSummary.issues.length) issues.addRow(['', '', 'No unresolved calculation issues.']);
    issues.columns.forEach(c => { c.width = 35; });
    issues.getColumn(3).width = 90;
  }
  const summary = wb.addWorksheet('By store');
  summary.addRow(['Store', 'Orders tracked', 'Approved disputed charges ($)', 'Denied ($)', 'Pending ($)', 'Uncertain ($)', 'Not yet verified ($)', 'Verified refunds ($)']);
  const entries = Object.values(ledger.orders);
  for (const store of [...new Set(entries.map(r => r.store))].sort()) {
    const rows = entries.filter(r => r.store === store);
    const t = totals(rows);
    const refunds = rows.filter(r => r.status === 'approved' && Number.isSafeInteger(r.approvedAmountCents)).reduce((sum, r) => sum + r.approvedAmountCents, 0);
    summary.addRow([store, rows.length, t.approved / 100, t.denied / 100, t.pending / 100, t.uncertain / 100, t.observed / 100, refunds / 100]);
  }
  const t = totals(entries);
  const totalRefunds = entries.filter(r => r.status === 'approved' && Number.isSafeInteger(r.approvedAmountCents)).reduce((sum, r) => sum + r.approvedAmountCents, 0);
  summary.addRow(['TOTAL', entries.length, t.approved / 100, t.denied / 100, t.pending / 100, t.uncertain / 100, t.observed / 100, totalRefunds / 100]).font = { bold: true };
  summary.addRow([]);
  summary.addRow(['Cumulative tracked orders. Approval is the portal status; bank/payout reconciliation is separate.']);
  summary.addRow(['Status last checked per order appears on the Orders sheet. Unknown reads preserve the last confirmed status.']);
  const detail = wb.addWorksheet('Orders');
  detail.addRow(['Store', 'Order URL', 'Disputed amount ($)', 'Last confirmed status', 'Filed at', 'Last checked at', 'Latest observation', 'Verified refund ($)']);
  for (const row of entries) detail.addRow([row.store, row.href, row.amountCents / 100, row.status, row.filedAt || '', row.lastCheckedAt || '', row.lastObservedStatus || '', Number.isSafeInteger(row.approvedAmountCents) ? row.approvedAmountCents / 100 : null]);
  if (reviewPlan) {
    const review = wb.addWorksheet('Evidence review');
    review.addRow(['Store', 'Order link', 'Charge ($)', 'Reported issue', 'Items to dispute', 'Supporting records / evidence', 'Reason to select', 'Reviewed explanation', 'Next action']);
    for (const row of [...reviewPlan.orders].sort((a, b) => b.amountCents - a.amountCents)) {
      review.addRow([row.store, row.href, row.amountCents / 100, row.reported, '', '', '', row.description,
        'Check delivery date and portal deadline; match exact charged items and reason to receipt, packing or pickup records. Do not claim evidence that is unavailable.']);
    }
    review.columns.forEach(c => { c.width = 34; c.alignment = { wrapText: true }; });
    review.getColumn(3).numFmt = '$#,##0.00';
    review.getColumn(9).width = 70;
  }
  for (const sheet of [summary, detail]) {
    sheet.getRow(1).font = { bold: true, color: { argb: 'FFFFFFFF' } };
    sheet.getRow(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF243746' } };
    sheet.views = [{ state: 'frozen', ySplit: 1 }];
    sheet.columns.forEach(c => { c.width = 23; });
  }
  detail.getColumn(2).width = 75;
  for (let column = 3; column <= 7; column++) summary.getColumn(column).numFmt = '$#,##0.00';
  detail.getColumn(3).numFmt = '$#,##0.00';
  detail.getColumn(8).numFmt = '$#,##0.00';
  summary.getColumn(8).numFmt = '$#,##0.00';
  return wb;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const data = path.resolve(process.env.DOORDASH_DATA_DIR || path.join(root, 'data'));
  const ledger = readLedger(path.join(data, 'ledger.json'));
  const out = path.join(root, 'reports', `DoorDash-Tracker-${new Date().toISOString().replace(/[:.]/g, '-')}.xlsx`);
  fs.mkdirSync(path.dirname(out), { recursive: true });
  const summaryPath = path.join(root, 'reports', 'biweekly-summary.json');
  const reviewPath = path.join(data, 'review-plan.json');
  const snapshotPath = path.join(data, 'tracker-snapshot.json');
  const financialPath = path.join(data, 'financial-reconciliation.json');
  await buildReport(ledger, fs.existsSync(summaryPath) ? JSON.parse(fs.readFileSync(summaryPath)) : null,
    fs.existsSync(reviewPath) ? JSON.parse(fs.readFileSync(reviewPath)) : null,
    fs.existsSync(snapshotPath) ? JSON.parse(fs.readFileSync(snapshotPath)) : null,
    fs.existsSync(financialPath) ? JSON.parse(fs.readFileSync(financialPath)) : null).xlsx.writeFile(out);
  console.log(`Saved ${out}`);
}
