import assert from 'node:assert/strict';
import test from 'node:test';


globalThis.window = { location: { search: '?mock=1' } };

const { CONFIG } = await import('../js/config.js');


test('mock data requires an explicit query opt-in', () => {
  assert.equal(CONFIG.USE_MOCK, true);
});
