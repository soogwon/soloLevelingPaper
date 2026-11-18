import test from 'node:test';
import assert from 'node:assert/strict';
const listeners = new Map();
globalThis.window = { location: { search: '', origin: 'https://example.test' },
  addEventListener: (n, f) => listeners.set(n, f), removeEventListener: n => listeners.delete(n), dispatchEvent() {} };
globalThis.document = { cookie: 'csrf_token=synthetic' };
const { default: page } = await import('../js/pages/remote-learning.js');
const context = '00000000-0000-0000-0000-000000000580';
const record = { context_id: context, learning_record_id: 'record', status: 'reading', server_version_id: 'v1', publication_id: 'pub' };
const consent = { notice: '<script>동의 안내</script>', notice_version: 'remote-annotation-storage-v1', granted: false };
function view() {
  const nodes = new Map();
  return { isConnected: true, innerHTML: '', textContent: '',
    querySelector(s) { if (!nodes.has(s)) nodes.set(s, { disabled: false, checked: false }); return nodes.get(s); },
    querySelectorAll() { return []; } };
}
function response(data) { return { ok: true, status: 200, json: async () => data }; }
function data(url) { return String(url).endsWith('/learning') ? record : String(url).endsWith('/consent') ? consent : { items: [], next_cursor: null }; }

test('조회는 학습 시작이나 동의를 만들지 않고 고지를 이스케이프한다', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, opts) => { calls.push(opts.method); return response(data(url)); });
  const v = view(); await page({ view: v, params: { contextId: context } });
  assert.deepEqual(calls, ['GET', 'GET', 'GET']);
  assert.match(v.innerHTML, /&lt;script&gt;동의 안내/);
  assert.doesNotMatch(v.innerHTML, /<script>/);
  await v.querySelector('[data-grant]').onclick();
  assert.equal(calls.length, 3);
  listeners.get('pagehide')();
});

test('로그아웃 후 늦은 응답은 표시와 후속 조회를 모두 차단한다', async t => {
  let resolve; let count = 0;
  t.mock.method(globalThis, 'fetch', () => { count++; return new Promise(r => { resolve = r; }); });
  const v = view(); const pending = page({ view: v, params: { contextId: context } });
  listeners.get('auth:logout')(); const before = v.innerHTML;
  resolve(response(record)); await pending;
  assert.equal(v.innerHTML, before); assert.equal(v.textContent, ''); assert.equal(count, 1);
});

test('동의 응답 유실 뒤 자동 재전송하지 않고 조회로 복구한다', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    calls.push(opts.method); if (opts.method === 'POST') throw Error('유실'); return response(data(url));
  });
  const v = view(); await page({ view: v, params: { contextId: context } });
  v.querySelector('[data-check]').checked = true;
  await v.querySelector('[data-grant]').onclick();
  assert.match(v.innerHTML, /자동 재전송하지/);
  await v.querySelector('[data-refresh]').onclick();
  assert.equal(calls.filter(m => m === 'POST').length, 1);
  listeners.get('pagehide')();
});

test('다른 맥락 응답은 기록·동의에 반영하지 않는다', async t => {
  let count = 0;
  t.mock.method(globalThis, 'fetch', async () => { count++; return response({ ...record, context_id: 'other' }); });
  const v = view(); await page({ view: v, params: { contextId: context } });
  assert.equal(count, 1); assert.doesNotMatch(v.innerHTML, /기록 ID: record/);
  listeners.get('pagehide')();
});
