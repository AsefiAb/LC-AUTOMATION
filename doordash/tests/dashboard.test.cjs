const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');

function load(name) {
  const filename = path.resolve(__dirname, '..', 'src', 'lib', name + '.ts');
  const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true },
  }).outputText;
  const mod = new Module(filename, module);
  mod.filename = filename;
  mod.paths = Module._nodeModulePaths(path.dirname(filename));
  mod._compile(output, filename);
  return mod.exports;
}
const { parseCsv } = load('parse');
const { assess } = load('rules');
const { draftDispute } = load('draft');
const { parsePlan } = load('plan');

test('CSV amounts, dates and photo values are parsed exactly', () => {
  const [row] = parseCsv('Order ID,Store,Amount,Date,Photo\n123,CO-1,"-$1,234.56",9/17/2026,maybe', 'doordash');
  assert.equal(row.amount, 1234.56);
  assert.equal(row.orderDate, '2026-09-17');
  assert.equal(row.disputeDeadline, '');
  assert.equal(row.hasPhotoEvidence, false);
});
test('invalid, duplicate and ambiguous CSV inputs fail visibly', () => {
  for (const csv of [
    'Amount\n10', 'Order ID,Amount\n1,12abc',
    'Order ID,Amount\n1,10\n1,20', 'Order ID,Amount,Date\n1,10,2026-02-30',
  ]) assert.throws(() => parseCsv(csv, 'doordash'));
});
test('store identity prevents order collisions across stores', () => {
  const rows = parseCsv('Order ID,Store,Amount\n1,CO-1,10\n1,TX-1,10', 'doordash');
  assert.notEqual(rows[0].id, rows[1].id);
});
test('draft and review do not invent fraud, inspection or recovery probabilities', () => {
  const [row] = parseCsv('Order ID,Store,Amount\n1,CO-1,10', 'doordash');
  const review = assess(row, [row, row]);
  assert.equal(review.recoverable, 10);
  assert.ok(review.reasons.some(s => /unknown/.test(s)));
  assert.doesNotMatch(draftDispute(row), /our kitchen|bagged|sealed|refund abuse|available on request/i);
  assert.doesNotMatch(review.reasons.join(' '), /abuse|unsubstantiated/);
});
test('a deadline remains open during the deadline date', () => {
  const now = new Date();
  const date = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
  assert.equal(assess({ amount: 10, disputeDeadline: date }, []).disputable, true);
});
test('review plan enforces store identity and rejects duplicate orders', () => {
  const row = { storeId: '1150071', store: 'CO-1', region: 'CO', href: 'https://www.doordash.com/merchant/financials/transactions/123?store_id=1150071', amountCents: 1234, reported: 'charge', description: 'Please review this charge.', approved: false };
  const plan = { version: 1, orders: [row] };
  assert.equal(parsePlan(JSON.stringify(plan)).charges[0].amount, 12.34);
  assert.throws(() => parsePlan(JSON.stringify({ ...plan, orders: [row, row] })));
  assert.throws(() => parsePlan(JSON.stringify({ ...plan, orders: [{ ...row, storeId: '999' }] })));
});
