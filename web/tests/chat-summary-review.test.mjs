import test from 'node:test';
import assert from 'node:assert/strict';

const listeners = new Map();
globalThis.window = {
  location: { origin: 'https://example.test', search: '' },
  addEventListener: (event, fn) => listeners.set(event, fn),
  removeEventListener: event => listeners.delete(event),
};
globalThis.document = { cookie: 'csrf_token=test' };
const { default: page } = await import('../js/pages/chat-summary-review.js');
const draft = { active: true, saved: false, binding: { paper_id: 'paper', server_version_id: 'v1' },
  items: [{ kind: 'method', text: '<b>비교 연구</b>', evidence_ids: ['chunk'] }],
  evidence_pages: { chunk: 6 } };

test('요약과 서버 페이지를 먼저 표시하고 확인한 경우에만 저장한다', async t => {
  const methods = [], button = {};
  const view = { isConnected: true, querySelector: () => button };
  t.mock.method(globalThis, 'fetch', async (_, options) => {
    methods.push(options.method);
    return { ok: true, status: 200, json: async () => ({ ...draft, saved: options.method === 'POST' }) };
  });
  await page({ view, params: { requestKey: 'key' } });
  assert.deepEqual(methods, ['GET']);
  assert.match(view.innerHTML, /PDF 6쪽/);
  assert.match(view.innerHTML, /&lt;b&gt;비교 연구&lt;\/b&gt;/);
  await button.onclick();
  assert.deepEqual(methods, ['GET', 'POST']);
  assert.match(view.innerHTML, /요약을 저장했어요/);
  listeners.get('pagehide')();
});
