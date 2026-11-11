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

test('텍스트만 전달된 안내는 카드에서 다시 준비하고 동의 후 작업 ID를 표시한다', async () => {
  const {app, el} = setup();
  const args = {source: {kind: 'local_file', relative_path: 'sample.pdf'}, request_key: 'win-001'};
  const preview = {status: 'confirmation_required', notice: '전송 안내', ...args};
  const calls = [];
  app.callServerTool = async call => {
    calls.push(call);
    if (call.name === 'prepare_paper_registration') {
      assert.deepEqual(call.arguments, args);
      return {structuredContent: preview, _meta: {confirmation_token: 'private-token'}};
    }
    return {content: [{type: 'text', text: JSON.stringify({job_id: 'job-276', status: 'processing'})}]};
  };
  await app.ontoolresult({content: [{type: 'text', text: JSON.stringify(preview)}]});
  assert.equal(el('filename').textContent, 'sample.pdf');
  assert.equal(el('agree').disabled, false);
  assert.equal(el('confirm').disabled, true);
  assert.deepEqual(calls.map(call => call.name), ['prepare_paper_registration']);
  el('agree').checked = true;
  el('agree').onchange();
  await el('confirm').onclick();
  assert.deepEqual(calls[1], {name: 'add_paper', arguments: {...args, confirmation_token: 'private-token'}});
  assert.match(el('status').textContent, /job-276/);
});

test('입력으로 안내를 다시 준비해도 비공개 토큰이 없으면 잠금을 유지한다', async () => {
  const {app, el} = setup();
  const args = {source: {kind: 'local_file', relative_path: 'sample.pdf'}, request_key: 'r'};
  app.ontoolinput({arguments: args});
  app.callServerTool = async () => ({structuredContent: {...args, notice: '안내', status: 'confirmation_required'}});
  await app.ontoolresult({content: [{type: 'text', text: '안내'}]});
  assert.equal(el('agree').disabled, true);
  assert.equal(el('confirm').disabled, true);
  assert.match(el('status').textContent, /다시 요청/);
});
