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

test('읽기 상태 응답 유실은 동일 키·입력으로만 재시도한다', async t => {
  const bodies = []; let first = true;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') {
      bodies.push(JSON.parse(opts.body));
      if (first) { first = false; throw Error('유실'); }
      return response({ state: { ...record, revision: 2 }, applied_revision: 2, replayed: true });
    }
    return response(String(url).endsWith('/learning') ? { ...record, revision: 1 } : data(url));
  });
  const v = view(); await page({ view: v, params: { contextId: context } });
  v.querySelector('[data-progress-status]').value = 'completed';
  v.querySelector('[data-progress-page]').value = '2';
  await v.querySelector('[data-progress-save]').onclick();
  assert.match(v.innerHTML, /같은 요청으로 결과 확인/);
  await v.querySelector('[data-progress-save]').onclick();
  assert.equal(bodies.length, 1);
  await v.querySelector('[data-refresh]').onclick();
  await v.querySelector('[data-progress-retry]').onclick();
  assert.equal(bodies.length, 2); assert.deepEqual(bodies[0], bodies[1]);
  assert.equal(bodies[0].pdf_page, 2); assert.equal(bodies[0].expected_revision, 1);
  listeners.get('pagehide')();
});

test('읽기 상태 저장 중 로그아웃하면 늦은 응답과 후속 조회를 버린다', async t => {
  let resolve; let count = 0;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    count++;
    if (opts.method === 'POST') return new Promise(r => { resolve = r; });
    return response(String(url).endsWith('/learning') ? { ...record, revision: 1 } : data(url));
  });
  const v = view(); await page({ view: v, params: { contextId: context } });
  v.querySelector('[data-progress-status]').value = 'reading';
  v.querySelector('[data-progress-page]').value = '';
  const pending = v.querySelector('[data-progress-save]').onclick();
  listeners.get('auth:logout')(); const before = v.innerHTML;
  resolve(response({ state: { ...record, revision: 2 } })); await pending;
  assert.equal(count, 4); assert.equal(v.innerHTML, before); assert.equal(v.textContent, '');
});

test('충돌 후 최신 revision을 읽고 새로운 키로 다음 변경을 저장한다', async t => {
  const bodies = []; let revision = 1;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') {
      bodies.push(JSON.parse(opts.body));
      if (bodies.length === 1) {
        revision = 3;
        return new Response(JSON.stringify({ detail: { code: 'annotation_conflict' } }), { status: 409 });
      }
      return response({ state: { ...record, revision: 4 } });
    }
    return response(String(url).endsWith('/learning') ? { ...record, revision } : data(url));
  });
  const v = view(); await page({ view: v, params: { contextId: context } });
  v.querySelector('[data-progress-status]').value = 'completed';
  v.querySelector('[data-progress-page]').value = '1';
  await v.querySelector('[data-progress-save]').onclick();
  assert.match(v.innerHTML, /최신 상태를 확인/);
  await v.querySelector('[data-progress-save]').onclick();
  assert.equal(bodies[1].expected_revision, 3);
  assert.notEqual(bodies[0].request_key, bodies[1].request_key);
  listeners.get('pagehide')();
});

test('저장 응답의 접근 거부는 이전 기록과 보류 키를 폐기한다', async t => {
  let count = 0;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    count++;
    if (opts.method === 'POST') return new Response(JSON.stringify({ detail: { code: 'web_session_required' } }), { status: 403 });
    return response(String(url).endsWith('/learning') ? { ...record, revision: 1 } : data(url));
  });
  const v = view(); await page({ view: v, params: { contextId: context } });
  v.querySelector('[data-progress-status]').value = 'completed'; v.querySelector('[data-progress-page]').value = '1';
  await v.querySelector('[data-progress-save]').onclick();
  assert.match(v.textContent, /접근할 수 없습니다/);
  await v.querySelector('[data-progress-retry]').onclick();
  assert.equal(count, 4); assert.equal(listeners.size, 0);
});
