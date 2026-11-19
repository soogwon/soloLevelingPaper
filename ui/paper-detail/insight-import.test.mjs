import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { mountInsightImport } from './insight-import.js';

const html = await readFile(new URL('./insight-import-template.html', import.meta.url), 'utf8');
const item = {insight_id:'item', kind:'keyword', text:'<img src=x>', pdf_page:1, quote_original:'합성 원문'};
const selection = {local_context_id:'local', remote_context_id:'remote', scope:'paper', server:'server', items:[item]};
const reviewReply = {_meta:{review:{...selection, publication_id:'fixed', server_version_id:'version'},
  submission_token:'approval'}};
function setup(respond) {
  const doc = new JSDOM(html).window.document, calls = [];
  const app = {connect: async () => {}, callServerTool: async call => {
    calls.push(call);
    return respond ? respond(call) : reviewReply;
  }};
  mountInsightImport(app, doc);
  app.ontoolinput({arguments:{context_id:'local', remote_context_id:'remote'}});
  app.ontoolresult({_meta:{selection, selection_token:'select'}});
  const box = doc.querySelector('#items input');
  box.checked = true;
  box.onchange();
  return {app, calls, doc, el: id => doc.getElementById(id)};
}

test('표시문은 텍스트로 렌더링하고 명시적 확인 전에는 제출하지 않는다', async () => {
  const {doc, calls, el} = setup(call => call.name.startsWith('preview') ? reviewReply :
    {_meta:{receipt:{status:'saved', revision:1}}});
  assert.equal(doc.querySelector('img'), null);
  await el('preview').onclick();
  assert.match(el('review-text').textContent, /fixed/);
  await el('submit').onclick();
  assert.equal(calls.length, 1);
  el('agree').checked = true; el('agree').onchange();
  await el('submit').onclick();
  assert.equal(calls[1].arguments.submission_token, 'approval');
  assert.match(el('status').textContent, /리비전 1/);
});

test('입력이 바뀐 뒤 도착한 미리보기 응답으로 제출할 수 없다', async () => {
  let finish;
  const {app, calls, el} = setup(() => new Promise(resolve => { finish = resolve; }));
  const pending = el('preview').onclick();
  app.ontoolinput({arguments:{context_id:'new', remote_context_id:'another'}});
  finish(reviewReply);
  await pending;
  await el('submit').onclick();
  assert.equal(calls.length, 1);
  assert.equal(el('review').hidden, true);
});
