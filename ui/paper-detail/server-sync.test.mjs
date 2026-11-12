import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { mountServerSync } from './server-sync.js';

const html = await readFile(new URL('./server-sync-template.html', import.meta.url), 'utf8');
function setup() {
  const doc = new JSDOM(html).window.document, calls = [];
  const app = {connect: async () => {}, callServerTool: async call => {
    calls.push(call);
    return {structuredContent: call.name === 'sync_paper_to_server'
      ? {operation_id: 'op-1', status: 'synced'} : {status: 'cancelled'}};
  }};
  mountServerSync(app, doc);
  app.ontoolresult({structuredContent: {status: 'confirmation_required', notice: '메타데이터만 전송',
    paper_id: 'paper-1', operation_id: 'op-1', title: '<img src=x onerror=alert(1)>'},
    _meta: {confirmation_token: 'secret'}});
  return {doc, calls, app, el: id => doc.getElementById(id)};
}

test('명시적 체크와 클릭 전에는 동기화하지 않고 텍스트를 안전하게 표시한다', async () => {
  const {doc, calls, el} = setup();
  assert.equal(doc.querySelector('img'), null);
  assert.equal(el('confirm').disabled, true);
  await el('confirm').onclick(); assert.equal(calls.length, 0);
  el('agree').checked = true; el('agree').onchange();
  const first = el('confirm').onclick(); await el('confirm').onclick(); await first;
  assert.deepEqual(calls, [{name: 'sync_paper_to_server', arguments: {
    paper_id: 'paper-1', operation_id: 'op-1', confirmation_token: 'secret'}}]);
  assert.match(el('status').textContent, /op-1/);
});

test('취소는 동기화 도구를 호출하지 않는다', async () => {
  const {calls, el} = setup(); await el('cancel').onclick();
  assert.deepEqual(calls.map(call => call.name), ['cancel_paper_server_sync']);
});

test('실패 후 자동 재시도하지 않고 비밀 오류를 표시하지 않는다', async () => {
  const {app, el} = setup(); let calls = 0;
  app.callServerTool = async () => { ++calls; throw new Error('private'); };
  el('agree').checked = true; el('agree').onchange();
  await el('confirm').onclick(); await el('confirm').onclick();
  assert.match(el('status').textContent, /op-1/); assert.equal(calls, 1); assert.doesNotMatch(el('status').textContent, /private/);
});

test('새 논문 입력이나 호스트 취소 후 이전 카드로 승인할 수 없다', async () => {
  const {app, calls, el} = setup();
  for (const event of [() => app.ontoolinput({arguments: {paper_id: 'paper-2'}}), () => app.ontoolcancelled()]) {
    event();
    app.ontoolresult({structuredContent: {status: 'confirmation_required', notice: '안내',
      paper_id: 'paper-1', operation_id: 'old', title: 'old'}, _meta: {confirmation_token: 'old'}});
    el('agree').checked = true;
    await el('confirm').onclick();
    assert.equal(el('confirm').disabled, true);
    assert.equal(calls.length, 0);
  }
});
