import test from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { scanStore, prepare, readStatus, approvedRefundCents } from '../portal.mjs';

test('savings use explicit refunds and reject ambiguous or missing amounts', () => {
  assert.equal(approvedRefundCents('Order total $100.00. Dispute approved. Amount refunded: $35.50'), 3550);
  assert.equal(approvedRefundCents('Dispute approved. Original charge $100.00'), null);
  assert.equal(approvedRefundCents('Refund amount $10.00; Amount refunded $20.00'), null);
  assert.equal(approvedRefundCents('You disputed the error charge for this order and will receive a credit of $13.26.\nError charge credit (includes tax)\n+$13.26'), 1326);
});

// Isolated browser with all network requests intercepted; never contacts DoorDash.
test('portal scan paginates and dispute form touches only its own dialog', async () => {
  const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'chrome' } : {}) });
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(2000);
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'https://www.doordash.com') return route.abort();
      if (url.pathname.endsWith('/transactions')) return route.fulfill({ contentType: 'text/html', body: `
        <button id="date" onclick="document.querySelector('#dates').hidden=false">Last 7 days</button>
        <div id="dates" hidden><button role="menuitem" onclick="document.querySelector('#date').textContent='Last 30 days';document.querySelector('#dates').hidden=true">Last 30 days</button></div>
        <button onclick="document.querySelector('#menu').hidden=false">Transaction types</button>
        <div id="menu" hidden><button role="menuitemcheckbox" aria-checked="false" onclick="this.setAttribute('aria-checked','true')">Error charge</button>
        <button role="menuitemcheckbox" aria-checked="false" onclick="this.setAttribute('aria-checked','true')">Adjustment</button></div>
        <button id="apply">Apply</button>
        <table><thead><tr><th>Transaction ID</th><th>Date</th><th>Transaction type</th><th>Error charges</th><th>Order ID</th><th>Net total</th></tr></thead><tbody id="rows"></tbody></table><p id="count"></p>
        <button id="next">Next page</button>
        <script>
          document.querySelector('#apply').onclick = () => { document.querySelector('#rows').innerHTML = '<tr><td>tx1</td><td>9/22/2026</td><td>Error charge</td><td>-$1,234.56</td><td><a href="/merchant/financials/transactions/one">one</a></td><td>-$9.00</td></tr>'; document.querySelector('#count').textContent='1-1 of 2 results'; };
          document.querySelector('#next').onclick = function() { document.querySelector('#rows').innerHTML = '<tr><td>tx2</td><td>9/22/2026</td><td>Error charge</td><td>-$5.00</td><td><a href="/merchant/financials/transactions/two">two</a></td><td>-$9.00</td></tr>'; document.querySelector('#count').textContent='2-2 of 2 results'; this.disabled = true; };
        </script>` });
      return route.fulfill({ contentType: 'text/html', body: `
        <input id="outside" type="checkbox"><button onclick="document.querySelector('[role=dialog]').hidden=false">Dispute charge</button>
        <div role="dialog" hidden><input type="checkbox"><button id="choose" onclick="document.querySelector('[role=menuitem]').hidden=false">Choose an option</button>
        <button role="menuitem" hidden onclick="document.querySelector('#choose').textContent='Other reason';this.hidden=true">Other reason</button>
        <textarea></textarea><button onclick="document.querySelector('[role=dialog]').remove();document.querySelector('#status').textContent='Dispute pending'">Submit for review</button></div><p id="status"></p>` });
    });
    const rows = await scanStore(page, { storeId: '1150071', label: 'CO-1', region: 'CO' });
    assert.equal(rows.length, 2);
    assert.equal(rows[0].amountCents, 123456);
    assert.equal(rows[1].amountCents, 500);
    const form = await prepare(page, rows[0]);
    assert.equal(await page.locator('#outside').isChecked(), false);
    assert.equal(await page.locator('textarea').inputValue(), rows[0].description);
    await form.submit();
    assert.equal(await form.status(), 'pending');
    await page.setContent('<p>Dispute approved</p><p>Dispute denied</p>');
    assert.equal(await readStatus(page), 'unknown');
  } finally { await browser.close(); }
});

test('multi-page scan counts all transactions, groups multiple charges per order, and rejects truncated results', async () => {
  const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'chrome' } : {}) });
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(2000);
    let broken = false;
    await page.route('**/*', route => route.fulfill({ contentType: 'text/html', body: `
      <button id="date" onclick="document.querySelector('#dates').hidden=false">Last 7 days</button>
      <div id="dates" hidden><button role="menuitem" onclick="document.querySelector('#date').textContent='Last 30 days';document.querySelector('#dates').hidden=true">Last 30 days</button></div>
      <button onclick="document.querySelector('#menu').hidden=false">All transaction types</button>
      <div id="menu" hidden><button role="menuitemcheckbox" aria-checked="false" onclick="this.setAttribute('aria-checked','true')">Error charge</button></div>
      <button id="apply">Apply</button>
      <table><thead><tr><th>Transaction ID</th><th>Date</th><th>Transaction type</th><th>Error charges</th><th>Order ID</th><th>Net total</th></tr></thead><tbody></tbody></table>
      <p id="count"></p><button id="next" aria-label="go to next page">Next</button>
      <script>
        let page=0;
        function draw(){
          const first=page*20, last=Math.min(first+20,65);
          document.querySelector('tbody').innerHTML=Array.from({length:last-first},(_,n)=>{
            const i=first+n; const order=i===64?'order-0':'order-'+i;
            return '<tr><td>tx'+i+'</td><td>9/22/2026</td><td>Error charge</td><td>-$1.00</td><td><a href="/merchant/financials/transactions/'+order+'">'+order+'</a></td><td>-$999.00</td></tr>';
          }).join('');
          document.querySelector('#count').textContent=(first+1)+'-'+last+' of 65 results';
          document.querySelector('#next').disabled=${broken}||last===65;
        }
        document.querySelector('#apply').onclick=draw;
        document.querySelector('#next').onclick=()=>{page++;draw()};
      </script>` }));
    const store = { storeId: '1150071', label: 'CO-1', region: 'CO' };
    const rows = await scanStore(page, store);
    assert.equal(rows.length, 64);
    assert.equal(rows[0].amountCents, 200);
    assert.equal(rows[0].chargeTransactions.length, 2);
    assert.equal(rows.coverage.readTransactions, 65);
    assert.equal(rows.coverage.pages, 4);
    assert.equal(rows.coverage.complete, true);
    broken = true;
    await assert.rejects(scanStore(page, store), /before all transactions/);
  } finally { await browser.close(); }
});
