import test from 'node:test';
import assert from 'node:assert/strict';
import { parseCloudSession } from '../browser.mjs';

test('cloud connection requires a nonempty DoorDash-only session without echoing secrets', () => {
  for (const raw of ['', 'secret-invalid-json', '{}', '{"cookies":[],"origins":[]}']) {
    assert.throws(() => parseCloudSession(raw), e => !e.message.includes('secret-invalid-json'));
  }
  const state = { cookies: [{ name: 'test', value: 'fake-token', domain: '.doordash.com' }], origins: [] };
  assert.deepEqual(parseCloudSession(JSON.stringify(state)), state);
  for (const domain of ['doordash.com.evil.test', '.google.com']) {
    assert.throws(() => parseCloudSession(JSON.stringify({ ...state, cookies: [{ ...state.cookies[0], domain }] })));
  }
});
