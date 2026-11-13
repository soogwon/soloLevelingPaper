import assert from 'node:assert/strict';
import test from 'node:test';


globalThis.window = { location: { search: '?mock=1' } };

const { CONFIG } = await import('../js/config.js');


test('mock data requires an explicit query opt-in', () => {
  assert.equal(CONFIG.USE_MOCK, true);
});

test('예시 모드에서 네트워크 없이 논문 목록과 상세를 조회한다', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', () => { throw new Error('unexpected fetch'); });
  const { api } = await import('../js/api/index.js');
  const { items } = await api.listPapers();
  assert.ok(items.length);
  assert.equal((await api.getPaper(items[0].paper_id)).paper_id, items[0].paper_id);
  assert.equal(fetch.mock.callCount(), 0);
});
