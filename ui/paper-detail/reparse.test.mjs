import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { mountReparse } from './reparse.js';

const html = await readFile(new URL('./reparse-template.html', import.meta.url), 'utf8');
const args = {context_id:'old', request_key:'first'};
const preview = {structuredContent:{...args, status:'AVAILABLE', filename:'합성.pdf',
  notice:'원문·비용 안내', translation:{provider:'test', model:'synthetic'}}, _meta:{confirmation_token:'secret'}};

function card(storage, handler) {
  const doc = new JSDOM(html).window.document;
  const calls = [];
  const app = {connect:async () => {}, callServerTool:async request => {
    calls.push(request); return handler(request);
  }};
  mountReparse(app, doc, storage);
  app.ontoolinput({arguments:args});
  app.ontoolresult(preview);
  const el = id => doc.getElementById(id);
  const agree = () => { el('agree').checked = true; el('agree').onchange(); };
  return {app, calls, el, agree};
}

test('응답 유실 후 새 카드가 기존 키로 조회하고 사용자 선택 후에만 새 맥락을 연다', async () => {
  const storage = new JSDOM('', {url:'https://synthetic.test'}).window.localStorage;
  const first = card(storage, () => { throw Error(); });
  await first.el('confirm').onclick();
  assert.equal(first.calls.length, 0);
  first.agree(); await first.el('confirm').onclick();
  await first.el('confirm').onclick();
  assert.equal(first.calls.length, 1);
  assert.equal(storage.getItem(storage.key(0)), 'first');
  const second = card(storage, ({name}) => ({structuredContent: name === 'get_paper_reparse'
    ? {...args, status:'COMPLETED', job_id:'job', result_available:true, new_embedding_set_id:'index'}
    : {job_id:'job', context_id:'new'}}));
  assert.equal(second.el('confirm').disabled, true);
  assert.equal(second.el('select').disabled, true);
  await second.el('refresh').onclick();
  assert.deepEqual(second.calls, [{name:'get_paper_reparse', arguments:args}]);
  await second.el('select').onclick();
  assert.deepEqual(second.calls[1], {name:'select_reparsed_context', arguments:{...args, job_id:'job'}});
  assert.match(second.el('status').textContent, /new/);
});

test('새 입력과 호스트 취소 뒤 이전 동의로 제출할 수 없다', async () => {
  const c = card(null, () => {throw Error();});
  c.agree();
  c.app.ontoolinput({arguments:{...args, request_key:'other'}});
  c.app.ontoolresult(preview);
  await c.el('confirm').onclick();
  c.app.ontoolcancelled();
  c.app.ontoolresult(preview);
  await c.el('confirm').onclick();
  assert.equal(c.calls.length, 0);
});

test('접수 전 조회 뒤 같은 카드에서 다시 동의해야 한 번만 제출한다', async () => {
  const storage = new JSDOM('', {url:'https://synthetic.test'}).window.localStorage;
  const c = card(storage, ({name}) => ({structuredContent:{...args,
    status:name === 'get_paper_reparse' ? 'PREPARED' : 'ACCEPTED',
    ...(name === 'submit_paper_reparse' ? {job_id:'job'} : {})}}));
  c.agree();
  await c.el('refresh').onclick();
  assert.match(c.el('status').textContent, /다시 동의/);
  assert.equal(c.el('agree').disabled, false);
  assert.equal(c.el('agree').checked, false);
  assert.equal(c.el('confirm').disabled, true);
  assert.equal(storage.length, 0);
  await c.el('confirm').onclick();
  assert.equal(c.calls.length, 1);
  c.agree();
  await c.el('confirm').onclick();
  await c.el('confirm').onclick();
  assert.deepEqual(c.calls, [
    {name:'get_paper_reparse', arguments:args},
    {name:'submit_paper_reparse', arguments:{...args, confirmation_token:'secret'}},
  ]);
});

test('반복 조회는 동의나 접수를 만들지 않고 같은 준비 토큰만 유지한다', async () => {
  const storage = new JSDOM('', {url:'https://synthetic.test'}).window.localStorage;
  const c = card(storage, () => ({structuredContent:{...args, status:'PREPARED'}}));
  for (let i = 0; i < 2; i++) {
    c.agree();
    await c.el('refresh').onclick();
    assert.equal(c.el('agree').checked, false);
    assert.equal(c.el('confirm').disabled, true);
    assert.equal(c.el('agree').disabled, false);
  }
  assert.equal(storage.length, 0);
  assert.ok(c.calls.every(call => call.name === 'get_paper_reparse'));
});

for (const response of [
  {isError:true},
  {structuredContent:{...args, context_id:'other', status:'PREPARED'}},
  {structuredContent:{...args, status:'ACCEPTED', job_id:'job'}},
  {structuredContent:{...args, status:'BLOCKED'}},
]) {
  test(`조회 실패·다른 맥락·접수·차단 응답은 동의를 복원하지 않는다: ${JSON.stringify(response)}`, async () => {
    const storage = new JSDOM('', {url:'https://synthetic.test'}).window.localStorage;
    const c = card(storage, () => response);
    c.agree();
    await c.el('refresh').onclick();
    assert.equal(c.el('agree').disabled, true);
    assert.equal(c.el('confirm').disabled, true);
    await c.el('confirm').onclick();
    assert.equal(c.calls.length, 1);
  });
}

test('제출 응답 유실 후 PREPARED를 받아도 토큰이나 접수 권한을 복원하지 않는다', async () => {
  const storage = new JSDOM('', {url:'https://synthetic.test'}).window.localStorage;
  const c = card(storage, ({name}) => {
    if (name === 'submit_paper_reparse') throw Error();
    return {structuredContent:{...args, status:'PREPARED'}};
  });
  c.agree();
  await c.el('confirm').onclick();
  await c.el('refresh').onclick();
  assert.equal(c.el('confirm').disabled, true);
  assert.equal(c.el('agree').disabled, true);
  assert.equal(storage.getItem('slp-reparse-v1:old'), 'first');
  assert.equal(c.calls.filter(call => call.name === 'submit_paper_reparse').length, 1);
});

test('늦은 준비 조회 응답이 새 입력이나 취소 뒤 토큰을 복구하지 않는다', async () => {
  for (const cancel of [false, true]) {
    let finish;
    const storage = new JSDOM('', {url:'https://synthetic.test'}).window.localStorage;
    const c = card(storage, () => new Promise(resolve => { finish = resolve; }));
    const pending = c.el('refresh').onclick();
    if (cancel) c.app.ontoolcancelled();
    else c.app.ontoolinput({arguments:{...args, request_key:'new'}});
    finish({structuredContent:{...args, status:'PREPARED'}});
    await pending;
    assert.equal(c.el('agree').disabled, true);
    assert.equal(c.el('confirm').disabled, true);
  }
});
