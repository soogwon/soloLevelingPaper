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
    paper_id: 'paper-1', operation_id: 'op-1', title: '<img src=x onerror=alert(1)>',
    confirmation_token: 'secret'}});
  return {doc, calls, app, el: id => doc.getElementById(id)};
}

test('호스트가 결과 메타를 제거해도 구조화 응답 토큰으로 승인할 수 있다', () => {
  const {el} = setup();
  assert.equal(el('agree').disabled, false);
});

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

test('실패 작업 재개는 같은 ID만 사용하고 새 동의를 만들지 않는다', async () => {
  const {app, el, calls} = setup();
  app.callServerTool = async call => {
    calls.push(call);
    return {structuredContent: {operation_id: 'op-1',
      status: call.name === 'retry_paper_server_sync' ? 'synced' : 'retryable_failure'}};
  };
  el('agree').checked = true;
  await el('confirm').onclick();
  assert.equal(el('retry').hidden, false);
  await el('retry').onclick();
  assert.deepEqual(calls.at(-1), {name: 'retry_paper_server_sync', arguments: {operation_id: 'op-1'}});
  assert.equal(calls.length, 2);
  assert.equal(el('retry').hidden, true);
});

test('응답 유실 뒤 상태 조회만으로 재전송하지 않는다', async () => {
  const {app, el, calls} = setup();
  app.callServerTool = async call => {
    calls.push(call);
    if (call.name === 'sync_paper_to_server') throw new Error('private');
    return {structuredContent: {operation_id: 'op-1', status: 'retryable_failure'}};
  };
  el('agree').checked = true;
  await el('confirm').onclick();
  assert.equal(el('retry').hidden, true);
  await el('refresh').onclick();
  assert.equal(calls.at(-1).name, 'get_paper_server_sync');
  assert.equal(el('retry').hidden, false);
  assert.equal(calls.length, 2);
});

test('재시작 후 작업 조회 카드에서도 명시적으로 재개한다', async () => {
  const {app, el, calls} = setup();
  app.ontoolinput({arguments: {operation_id: 'existing'}});
  app.ontoolresult({structuredContent: {operation_id: 'existing', status: 'retryable_failure'}});
  assert.equal(el('retry').hidden, false);
  assert.equal(el('confirm').disabled, true);
  assert.equal(calls.length, 0);
  app.callServerTool = async call => {
    calls.push(call);
    return {structuredContent: {operation_id: 'existing', status: 'synced'}};
  };
  await el('retry').onclick();
  assert.deepEqual(calls, [{name: 'retry_paper_server_sync', arguments: {operation_id: 'existing'}}]);
});

test('최초 상태 조회 오류 뒤에는 같은 작업을 다시 조회할 수 있다', async () => {
  const {app, el, calls} = setup();
  app.ontoolinput({arguments: {operation_id: 'existing'}});
  app.ontoolresult({isError: true, content: [{type: 'text', text: 'private-error'}]});
  assert.match(el('status').textContent, /상태를 확인하지 못했습니다/);
  assert.doesNotMatch(el('status').textContent, /private-error/);
  assert.equal(el('refresh').hidden, false);
  assert.equal(el('retry').hidden, true);
  assert.equal(calls.length, 0);
  app.callServerTool = async call => {
    calls.push(call);
    return {structuredContent: {operation_id: 'existing', status: 'synced'}};
  };
  await el('refresh').onclick();
  assert.deepEqual(calls, [{name: 'get_paper_server_sync', arguments: {operation_id: 'existing'}}]);
  assert.match(el('status').textContent, /synced/);
  assert.equal(el('retry').hidden, true);
});

for (const state of ['conflict', 'action_required', 'syncing', 'synced']) {
  test(`${state} 작업은 재개 버튼으로 전송하지 않는다`, async () => {
    const {app, el, calls} = setup();
    app.ontoolinput({arguments: {operation_id: 'existing'}});
    app.ontoolresult({structuredContent: {operation_id: 'existing', status: state}});
    await el('retry').onclick();
    assert.equal(calls.length, 0);
  });
}

test('재개 중 새 입력 뒤 늦은 응답은 이전 작업을 복원하지 않는다', async () => {
  const {app, el} = setup();
  app.ontoolinput({arguments: {operation_id: 'existing'}});
  app.ontoolresult({structuredContent: {operation_id: 'existing', status: 'retryable_failure'}});
  let finish;
  app.callServerTool = () => new Promise(resolve => { finish = resolve; });
  const retry = el('retry').onclick();
  app.ontoolinput({arguments: {paper_id: 'other'}});
  finish({structuredContent: {operation_id: 'existing', status: 'retryable_failure'}});
  await retry;
  assert.equal(el('retry').hidden, true);
  assert.equal(el('status').textContent, '');
});
