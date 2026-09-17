import test from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { scanStore, prepare, readStatus } from '../portal.mjs';

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
        <button onclick="document.querySelector('#menu').hidden=false">Transaction types</button>
        <div id="menu" hidden><button role="menuitemcheckbox" aria-checked="false" onclick="this.setAttribute('aria-checked','true')">Error charge</button>
        <button role="menuitemcheckbox" aria-checked="false" onclick="this.setAttribute('aria-checked','true')">Adjustment</button></div>
        <button id="apply">Apply</button>
        <table><tbody id="rows"></tbody></table>
        <button id="next">Next page</button>
        <script>
          document.querySelector('#apply').onclick = () => { document.querySelector('#rows').innerHTML = '<tr><td><a href="/merchant/financials/transactions/one">Error charge pizza $0.00 -$1,234.56</a></td></tr>'; };
          document.querySelector('#next').onclick = function() { document.querySelector('#rows').innerHTML = '<tr><td><a href="/merchant/financials/transactions/two">Adjustment pizza $0.00 -$5.00</a></td></tr>'; this.disabled = true; };
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
