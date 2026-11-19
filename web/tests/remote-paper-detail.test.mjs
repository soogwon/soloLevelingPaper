import test from 'node:test';
import assert from 'node:assert/strict';

const listeners = new Map();
globalThis.window = {
  location: { origin: 'https://example.test', search: '' },
  addEventListener: (name, listener) => listeners.set(name, listener),
  removeEventListener: name => listeners.delete(name),
  dispatchEvent() {},
};
globalThis.document = { cookie: '' };

const { remotePaperDetail } = await import('../js/lib/remote-paper-detail.js');
const id = value => `00000000-0000-0000-0000-${String(value).padStart(12, '0')}`;
const paper = id(1), version = 'v1', context = id(2), publication = id(3);
const contexts = { items: [{
  context_id: context, paper_id: paper, server_version_id: version, publication_id: publication,
  learning_record_id: id(4), status: 'reading', revision: 2,
  last_position: { pdf_page: 7 }, updated_at: '2026-11-20T01:02:03Z',
}], next_cursor: null };
const summary = { state: 'completed', context_id: context, request_key: id(5), result: {
  context_id: context, binding: { context_id: context, paper_id: paper,
    server_version_id: version, publication_id: publication },
  items: [{ kind: 'claim', text: '<script>원격 요약</script>', evidence_ids: [id(6)] }],
} };
const annotations = { items: [{
  context_id: context, annotation_id: id(7), kind: 'note', body: '<b>원격 메모</b>',
  creation_origin: 'user', revision: 1, created_at: '2026-11-20T01:00:00Z',
  updated_at: '2026-11-20T01:00:00Z', segments: [{ target: 'translation', quote_snapshot: '번역 근거' }],
}], next_cursor: null, total_count: 1 };
const response = data => ({ ok: true, status: 200, json: async () => data });
function view() {
  const nodes = new Map();
  return { isConnected: true, innerHTML: '', textContent: '',
    querySelector(selector) { if (!nodes.has(selector)) nodes.set(selector, {}); return nodes.get(selector); },
    querySelectorAll() { return []; } };
}

test('대표 원격 맥락을 기존 상세 카드 스타일의 학습·요약·기록에 연결한다', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    const value = String(url); calls.push([value, options.method]);
    if (value.includes('/contexts?')) return response(contexts);
    if (value.includes('/remote-summaries/')) return response(summary);
    if (value.includes('/annotations')) return response(annotations);
    throw Error(value);
  });
  const views = { progress: view(), summary: view(), notes: view() };
  await remotePaperDetail(views, paper, version);
  assert.ok(calls.every(([, method]) => method === 'GET'));
  assert.match(views.progress.innerHTML, /마지막 학습 기록/);
  assert.match(views.progress.innerHTML, /PDF 7페이지/);
  assert.match(views.summary.innerHTML, />요약</);
  assert.match(views.summary.innerHTML, /&lt;script&gt;원격 요약/);
  assert.match(views.notes.innerHTML, /메모·하이라이트/);
  assert.match(views.notes.innerHTML, /&lt;b&gt;원격 메모/);
  assert.match(views.notes.innerHTML, /번역 근거/);
  assert.doesNotMatch(views.summary.innerHTML + views.notes.innerHTML, /로컬 동기화|원격 저장 요약|원격 메모·하이라이트/);
  listeners.get('pagehide')();
});

test('원격 학습 맥락이 없으면 로컬 기록으로 조용히 대체하지 않는다', async t => {
  t.mock.method(globalThis, 'fetch', async () => response({ items: [], next_cursor: null }));
  const views = { progress: view(), summary: view(), notes: view() };
  await remotePaperDetail(views, paper, version);
  assert.match(views.progress.innerHTML, /저장된 원격 학습 기록이 없어요/);
  assert.match(views.summary.innerHTML, /저장된 요약이 없어요/);
  assert.match(views.notes.innerHTML, /아직 남긴 기록이 없어요/);
  listeners.get('pagehide')();
});

test('로그아웃 뒤 늦은 맥락 응답은 이전 계정 화면에 표시하지 않는다', async t => {
  let resolve;
  t.mock.method(globalThis, 'fetch', () => new Promise(done => { resolve = done; }));
  const views = { progress: view(), summary: view(), notes: view() };
  const pending = remotePaperDetail(views, paper, version);
  listeners.get('auth:logout')();
  resolve(response(contexts)); await pending;
  assert.equal(views.progress.textContent, '');
  assert.equal(views.summary.textContent, '');
  assert.equal(views.notes.textContent, '');
});
