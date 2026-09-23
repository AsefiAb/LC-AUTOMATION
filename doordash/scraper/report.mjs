import ExcelJS from 'exceljs';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readLedger, totals } from './state.mjs';

export function buildReport(ledger, abSummary = null, reviewPlan = null) {
  const wb = new ExcelJS.Workbook();
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
  await buildReport(ledger, fs.existsSync(summaryPath) ? JSON.parse(fs.readFileSync(summaryPath)) : null,
    fs.existsSync(reviewPath) ? JSON.parse(fs.readFileSync(reviewPath)) : null).xlsx.writeFile(out);
  console.log(`Saved ${out}`);
}
