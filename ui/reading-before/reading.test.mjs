import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { mountReading } from './reading.js';

const html = await readFile(new URL('./template.html', import.meta.url), 'utf8');
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
function snapshot(answer = null, context_id = 'ctx') {
  return {structuredContent: {context_id, phase:'before', total:3, written:answer ? 1 : 0,
    completed:answer?.status === 'completed' ? 1 : 0,
    items:['background','hypothesis','expectation'].map((id,i) => ({
      template:{template_id:'before.' + id, phase:'before', prompt:'질문 ' + i},
      answer:i === 0 && answer ? {...answer, context_id, phase:'before', template_id:'before.background'} : null,
    }))}};
}
async function setup(callServerTool) {
  const doc = new JSDOM(html).window.document;
  const app = {connect:async () => {}, callServerTool};
  mountReading(app, doc);
  app.ontoolinput({arguments:{context_id:'ctx'}});
  app.ontoolresult(snapshot());
  await tick();
  return {app,doc,inputs:() => [...doc.querySelectorAll('textarea')],
    buttons:() => [...doc.querySelectorAll('section button')]};
}
function edit(input, text) { input.value = text; input.oninput(); }

test('초안 저장·완료·재조회 시 다른 질문의 편집과 서버 집계가 보존된다', async () => {
  let answer;
  const calls = [];
  const {doc,inputs,buttons} = await setup(async request => {
    calls.push(request);
    if (request.name === 'save_reading_answer') answer = request.arguments;
    return snapshot(answer);
  });
  assert.equal(inputs().length, 3);
  buttons()[1].click();
  assert.equal(calls.length, 0);
  edit(inputs()[1], '작성 중');
  edit(inputs()[0], ' 초안 ');
  buttons()[0].click(); buttons()[0].click(); await tick();
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].arguments, {context_id:'ctx',phase:'before',template_id:'before.background',
    text:' 초안 ',status:'draft'});
  assert.match(doc.querySelector('#counts').textContent,/작성 1개 · 완료 0개/);
  edit(inputs()[0], '완료 답변');
  buttons()[1].click(); await tick();
  doc.querySelector('#refresh').click(); await tick();
  assert.match(doc.querySelector('#counts').textContent,/작성 1개 · 완료 1개/);
  assert.equal(inputs()[0].value,'완료 답변');
  assert.equal(inputs()[1].value,'작성 중');
  assert.equal(calls[2].name,'get_reading_questions');
});

test('저장 응답이 불명확하면 조회하기 전까지 재저장을 막는다', async () => {
  let count = 0;
  const {doc,inputs,buttons} = await setup(async () => ++count === 1 ? snapshot(null,'other') : snapshot());
  edit(inputs()[0],'보존할 답변'); buttons()[0].click(); await tick();
  assert.equal(inputs()[0].value,'보존할 답변');
  assert.ok(buttons()[0].disabled);
  doc.querySelector('#refresh').click(); await tick();
  assert.equal(count,2);
  assert.equal(buttons()[0].disabled,false);
});

test('context 변경 후 늦게 도착한 이전 저장 결과를 무시한다', async () => {
  let finish;
  const {app,inputs,buttons} = await setup(() => new Promise(resolve => { finish = resolve; }));
  edit(inputs()[0],'이전 답변'); buttons()[0].click();
  app.ontoolinput({arguments:{context_id:'other'}});
  app.ontoolresult(snapshot(null,'other'));
  finish(snapshot({text:'이전 답변',status:'draft'})); await tick();
  assert.equal(inputs()[0].value,'');
});
