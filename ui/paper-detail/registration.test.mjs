import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { mountRegistration } from './registration.js';
const html = await readFile(new URL('./registration-template.html', import.meta.url), 'utf8');
function setup() {
  const doc = new JSDOM(html).window.document, calls = [];
  const app = {connect: async () => {}, callServerTool: async call => {
    calls.push(call); return {structuredContent: {job_id: 'j', status: call.name === 'add_paper' ? 'processing' : 'cancelled'}};
  }};
  mountRegistration(app, doc);
  app.ontoolresult({structuredContent: {status: 'confirmation_required', notice: 'OpenAI 전송',
    source: {kind: 'local_file', relative_path: '<img src=x onerror=alert(1)>'}, request_key: 'r'}, _meta: {confirmation_token: 't'}});
  return {doc, calls, app, el: id => doc.getElementById(id)};
}
test('명시적인 선택과 클릭 전에는 등록하지 않으며 중복 클릭을 막는다', async () => {
  const {calls, el, doc} = setup();
  assert.equal(calls.length, 0);
  assert.equal(el('confirm').disabled, true);
  assert.equal(doc.querySelector('img'), null);
  await el('confirm').onclick();
  assert.equal(calls.length, 0);
  el('agree').checked = true; el('agree').onchange();
  const first = el('confirm').onclick();
  await el('confirm').onclick(); await first;
  assert.equal(calls.length, 1);
  assert.equal(calls[0].name, 'add_paper');
  assert.equal(calls[0].arguments.confirmation_token, 't');
  assert.match(el('status').textContent, /작업 ID: j/);
  assert.equal(el('confirm').disabled, true);
});
test('취소는 등록 도구를 호출하지 않는다', async () => {
  const {calls, el} = setup(); await el('cancel').onclick();
  assert.deepEqual(calls.map(x => x.name), ['cancel_paper_registration']);
  assert.equal(el('confirm').disabled, true);
});
test('실패 후 자동 재시도하지 않는다', async () => {
  const {app, el} = setup(); let calls = 0;
  app.callServerTool = async () => { ++calls; throw new Error('private'); };
  el('agree').checked = true; el('agree').onchange();
  await el('confirm').onclick(); await el('confirm').onclick();
  assert.equal(calls, 1); assert.match(el('status').textContent, /작업 목록/);
  assert.doesNotMatch(el('status').textContent, /private/);
});
test('잘못된 응답은 기존 확인 요청을 비활성화한다', () => {
  const {app, el} = setup(); app.ontoolresult({isError: true});
  assert.equal(el('confirm').disabled, true); assert.equal(el('notice').textContent, '');
});
