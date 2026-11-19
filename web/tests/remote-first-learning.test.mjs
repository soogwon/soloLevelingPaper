import test from 'node:test';
import assert from 'node:assert/strict';

const listeners = new Map();
globalThis.window = {
  location: {origin:'https://example.test', search:''},
  addEventListener: (name, handler) => listeners.set(name, handler),
  removeEventListener: name => listeners.delete(name),
};
globalThis.document = {cookie:''};
const { remoteFirstLearning } = await import('../js/lib/remote-first-learning.js');
const id = n => `00000000-0000-0000-0000-${String(n).padStart(12,'0')}`;
const paper = id(1), version = id(2), intent = id(3);
const notice = 'test-notice';
const consent = (purpose, n) => ({
  purpose, consent_id:id(n), notice_version:notice, state:'current', revision:1,
  granted_at:'2026-11-19T00:00:00Z', revoked_at:null,
});
function view() {
  const nodes = new Map();
  return {isConnected:true, innerHTML:'', textContent:'',
    querySelector(selector) {
      const attr = selector.slice(1, -1);
      if (!this.innerHTML.includes(attr)) return null;
      if (!nodes.has(selector)) nodes.set(selector, {});
      return nodes.get(selector);
    }};
}
const notFound = {status:404, code:'remote_processing_not_found'};
const source = {paper_id:paper, version_id:version, intent_id:intent, active_publication_id:null};
test('기존 등록본 동의, 같은 키 접수, 게시 후 Claude 첫 학습 경로', async () => {
  const target = view(), keys = [];
  let ai = null, publication = null;
  const client = {
    getRemoteProcessingSource: async () => ({...source, active_publication_id:publication}),
    getRemoteProcessing: async () => { throw notFound; },
    getRemoteUploadConsents: async () => ({
      intent_id:intent, notice_version:notice, storage_notice:'보관 안내',
      ai_provider_notice:'OpenAI 전송 안내', storage:consent('storage', 4), ai_provider:ai,
    }),
    grantRemoteUploadConsent: async (gotIntent, purpose, version) => {
      assert.deepEqual([gotIntent, purpose, version], [intent, 'ai_provider', notice]);
      return ai = consent('ai_provider', 5);
    },
    admitRemoteProcessing: async (gotIntent, body, key) => {
      assert.equal(gotIntent, intent);
      assert.deepEqual(body, {storage_consent_id:id(4), ai_consent_id:id(5)});
      keys.push(key);
      if (keys.length === 1) throw Error('response_lost');
      return {paper_id:paper, version_id:version, job_id:id(6), status:'queued'};
    },
  };
  await remoteFirstLearning(target, paper, version, client);
  assert.match(target.innerHTML, /OpenAI 전송 안내/);
  assert.doesNotMatch(target.innerHTML, /open_remote_paper/);
  await target.querySelector('[data-admit]').onclick();
  assert.equal(keys.length, 0);
  const checkbox = target.querySelector('[data-ai-check]');
  checkbox.checked = true;
  checkbox.onchange();
  await target.querySelector('[data-ai-grant]').onclick();
  assert.equal(keys.length, 0);
  await target.querySelector('[data-admit]').onclick();
  await target.querySelector('[data-admit]').onclick();
  assert.equal(keys.length, 2);
  assert.equal(keys[0], keys[1]);
  assert.match(target.innerHTML, /처리 대기 중/);
  publication = id(7);
  await target.querySelector('[data-check]').onclick();
  assert.match(target.innerHTML, /open_remote_paper.*start_remote_learning/);
  assert.match(target.innerHTML, new RegExp(version));
  listeners.get('pagehide')();
});

test('다른 판본과 로그아웃 뒤 도착한 공개 상태 응답을 사용하지 않는다', async () => {
  const target = view();
  await remoteFirstLearning(target, paper, version, {
    getRemoteProcessingSource: async () => ({...source, version_id:id(9), active_publication_id:id(7)}),
  });
  assert.doesNotMatch(target.innerHTML, /open_remote_paper/);
  listeners.get('pagehide')();
  let resolve;
  const next = view();
  const pending = remoteFirstLearning(next, paper, version, {
    getRemoteProcessingSource: () => new Promise(done => { resolve = done; }),
  });
  listeners.get('auth:logout')();
  const before = next.innerHTML;
  resolve({...source, active_publication_id:id(7)});
  await pending;
  assert.equal(next.innerHTML, before);
});

test('동의가 바뀌면 새 조회 후 새 키로 명시 접수한다', async () => {
  const target = view(), calls = [];
  let ai = consent('ai_provider', 5);
  const client = {
    getRemoteProcessingSource: async () => source,
    getRemoteProcessing: async () => { throw notFound; },
    getRemoteUploadConsents: async () => ({
      intent_id:intent, notice_version:notice, storage_notice:'보관 안내',
      ai_provider_notice:'AI 전송 안내', storage:consent('storage', 4), ai_provider:ai,
    }),
    admitRemoteProcessing: async (_intent, body, key) => {
      calls.push({body, key});
      if (calls.length === 1) throw Error('response_lost');
      return {paper_id:paper, version_id:version, job_id:id(6), status:'queued'};
    },
  };
  await remoteFirstLearning(target, paper, version, client);
  await target.querySelector('[data-admit]').onclick();
  ai = consent('ai_provider', 9);
  await target.querySelector('[data-check]').onclick();
  assert.equal(calls.length, 1);
  await target.querySelector('[data-admit]').onclick();
  assert.equal(calls.length, 2);
  assert.equal(calls[1].body.ai_consent_id, id(9));
  assert.notEqual(calls[1].key, calls[0].key);
  listeners.get('pagehide')();
});
