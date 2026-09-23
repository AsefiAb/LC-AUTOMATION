import { chromium } from 'playwright';

export function parseCloudSession(raw) {
  if (!raw) throw new Error('Missing DOORDASH_STORAGE_STATE GitHub secret; connect the merchant account before running.');
  let state;
  try { state = JSON.parse(raw); } catch { throw new Error('DOORDASH_STORAGE_STATE must contain valid browser session JSON.'); }
  const hostOK = host => host === 'doordash.com' || host.endsWith('.doordash.com');
  if (!state || !Array.isArray(state.cookies) || !state.cookies.length || !Array.isArray(state.origins)) {
    throw new Error('DoorDash session is empty or incomplete.');
  }
  if (state.cookies.some(c => typeof c.domain !== 'string' || !hostOK(c.domain.replace(/^\./, '')) || typeof c.value !== 'string') ||
      state.origins.some(o => { try { const u = new URL(o.origin); return u.protocol !== 'https:' || !hostOK(u.hostname); } catch { return true; } })) {
    throw new Error('Session must contain only DoorDash cookies and origins.');
  }
  return state;
}

export async function openBrowser() {
  // Actions must never depend on a user's running desktop browser.
  if (process.env.GITHUB_ACTIONS === 'true' || process.env.DOORDASH_BROWSER_MODE === 'cloud') {
    const storageState = parseCloudSession(process.env.DOORDASH_STORAGE_STATE);
    // A GitHub virtual display is also invisible to the owner's computer.
    const browser = await chromium.launch({ headless: !process.env.DISPLAY });
    try {
      const context = await browser.newContext({ storageState, viewport: { width: 1600, height: 1000 } });
      return { browser, context };
    } catch (error) { await browser.close(); throw error; }
  }
  const cdp = new URL(process.env.CDP_URL || 'http://127.0.0.1:9222');
  if (cdp.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(cdp.hostname) || cdp.username || cdp.password) {
    throw new Error('CDP_URL must point to Chrome on this computer');
  }
  const browser = await chromium.connectOverCDP(cdp.href, { timeout: 30000 });
  const context = browser.contexts()[0];
  if (!context) { await browser.close(); throw new Error('No Chrome session found'); }
  return { browser, context };
}
