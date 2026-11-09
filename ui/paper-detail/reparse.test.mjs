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
