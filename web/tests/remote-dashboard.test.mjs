import test from 'node:test';
import assert from 'node:assert/strict';
const listeners = new Map();
globalThis.window = { location: { search: '', origin: 'https://example.test' },
  addEventListener: (n, f) => listeners.set(n, f), removeEventListener: n => listeners.delete(n) };
globalThis.document = { cookie: '' };
const { default: page } = await import('../js/pages/remote-dashboard.js');
const id = n => '00000000-0000-0000-0000-' + String(n).padStart(12, '0');
const item = n => ({ context_id: id(n), title: '<script>합성</script>', server_version_id: 'v1', publication_id: 'pub',
  processing_status: 'published', reading_status: 'not_started', last_position: null, last_learning_updated_at: null,
  note_count: 0, highlight_count: 0 });
function view() { const nodes = new Map(); return { isConnected: true, innerHTML: '', textContent: '',
  querySelector(s) { if (!nodes.has(s)) nodes.set(s, {}); return nodes.get(s); } }; }
const response = data => new Response(JSON.stringify(data), { status: 200 });

test('20+1 조회와 새로고침은 중복 없이 읽기만 수행한다', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    calls.push([String(url), opts.method]);
    return response(new URL(url).searchParams.has('after') ? { items: [item(20), item(21)], next_cursor: null }
      : { items: Array.from({ length: 20 }, (_, i) => item(i + 1)), next_cursor: id(20) });
  });
  const v = view(); await page({ view: v });
  assert.equal((v.innerHTML.match(/이 학습 맥락 열기/g) || []).length, 20);
  assert.match(v.innerHTML, /&lt;script&gt;/); assert.match(v.innerHTML, /시작하지 않음/);
  await v.querySelector('[data-more]').onclick();
  assert.equal((v.innerHTML.match(/이 학습 맥락 열기/g) || []).length, 21);
  assert.doesNotMatch(v.innerHTML, /data-more/);
  await v.querySelector('[data-refresh]').onclick();
  assert.equal((v.innerHTML.match(/이 학습 맥락 열기/g) || []).length, 20);
  assert.ok(calls.every(([, method]) => method === 'GET'));
  listeners.get('pagehide')();
});

test('로그아웃 뒤 목록 응답은 표시하지 않는다', async t => {
  let resolve;
  t.mock.method(globalThis, 'fetch', () => new Promise(r => { resolve = r; }));
  const v = view(); const pending = page({ view: v });
  listeners.get('auth:logout')(); const before = v.innerHTML;
  resolve(response({ items: [item(1)], next_cursor: null })); await pending;
  assert.equal(v.textContent, ''); assert.equal(v.innerHTML, before);
});
