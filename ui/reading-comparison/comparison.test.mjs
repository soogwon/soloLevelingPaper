import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { mountComparison } from './comparison.js';
const html = await readFile(new URL('./template.html', import.meta.url), 'utf8');
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
function snapshot(context_id = 'ctx') {
  const session = (phase, keys) => ({context_id, phase, total:keys.length, written:0, completed:0,
    items:keys.map(key => ({template:{template_id:phase+'.'+key, phase, category:key,
      prompt:key, step:phase === 'before' ? 'preview' : 'review'}, answer:null}))});
  const before = session('before', ['background','hypothesis','expectation']);
  const after = session('after', ['claim','evidence','limitation','application']);
  after.items[0].answer = {context_id, phase:'after', template_id:'after.claim',
    text:'<img src=x onerror=alert(1)>', status:'completed', created_at:'2026-11-03T00:00:00Z', updated_at:'2026-11-03T01:00:00Z'};
  after.items[1].answer = {...after.items[0].answer, template_id:'after.evidence', text:'', status:'draft'};
  after.written = 2; after.completed = 1;
  return {structuredContent:{context_id,before,after,groups:[
    {group_id:'hypothesis_vs_paper',before:before.items[1],after:after.items.slice(0,2)},
    {group_id:'background_vs_limitation',before:before.items[0],after:[after.items[2]]},
    {group_id:'expectation_vs_application',before:before.items[2],after:[after.items[3]]},
  ]}};
}
async function setup(callServerTool = async () => snapshot(), timeoutMs = 15000) {
  const doc = new JSDOM(html).window.document;
  const app = {connect:async () => {}, callServerTool};
  mountComparison(app,doc,timeoutMs);
  app.ontoolinput({arguments:{context_id:'ctx'}});
  app.ontoolresult(snapshot()); await tick(); return {app,doc};
}
test('세 묶음과 1대다·미작성·빈 초안·수정 시각을 표시하고 HTML은 실행하지 않는다', async () => {
  const {doc} = await setup();
  assert.equal(doc.querySelectorAll('section').length,3);
  assert.equal(doc.querySelector('section').querySelectorAll('article').length,3);
  assert.match(doc.querySelector('#counts').textContent,/읽기 후: 작성 2\/4 · 완료 1\/4/);
  assert.match(doc.querySelector('#groups').textContent,/미작성/);
  assert.match(doc.querySelector('#groups').textContent,/작성 중/);
  assert.match(doc.querySelector('#groups').textContent,/2026-11-03T01:00:00Z/);
  assert.equal(doc.querySelectorAll('img').length,0);
  assert.match(doc.querySelector('pre').parentElement.parentElement.parentElement.textContent,/<img/);
  assert.equal(doc.querySelectorAll('textarea').length,0);
  assert.match(doc.querySelector('#notice').textContent,/현재 저장/);
});
test('재조회는 현재 맥락의 읽기 도구만 한 번 호출한다', async () => {
  const calls=[];
  const {doc}=await setup(async args => {calls.push(args); return snapshot();});
  doc.querySelector('#refresh').click(); doc.querySelector('#refresh').click(); await tick();
  assert.deepEqual(calls,[{name:'get_reading_comparison',arguments:{context_id:'ctx'}}]);
});
for (const [label, mutate] of [
  ['다른 맥락', r => {r.structuredContent.context_id='other';}],
  ['잘못된 집계', r => {r.structuredContent.after.written=0;}],
  ['누락된 묶음', r => {r.structuredContent.groups.pop();}],
  ['묶음과 세션 불일치', r => {r.structuredContent.groups[0].after[0]=structuredClone(r.structuredContent.after.items[0]); r.structuredContent.groups[0].after[0].answer.text='다른 답';}],
  ['뒤바뀐 시점', r => {r.structuredContent.after.phase='before';}],
]) test(label+' 응답은 이전 답변을 지우고 표시를 거부한다', async () => {
  const {app,doc}=await setup(); const result=snapshot(); mutate(result); app.ontoolresult(result);
  assert.equal(doc.querySelectorAll('article').length,0);
  assert.match(doc.querySelector('#notice').textContent,/확인하지 못/);
});
test('맥락 변경과 취소 뒤 늦은 재조회 결과는 표시하지 않는다', async () => {
  let finish;
  const {app,doc}=await setup(() => new Promise(resolve => {finish=resolve;}));
  doc.querySelector('#refresh').click();
  app.ontoolinput({arguments:{context_id:'other'}}); app.ontoolresult(snapshot('other'));
  finish(snapshot()); await tick();
  assert.equal(doc.querySelectorAll('article').length,7);
  app.ontoolcancelled(); app.ontoolresult(snapshot());
  assert.equal(doc.querySelectorAll('article').length,0);
  assert.ok(doc.querySelector('#refresh').disabled);
});
test('조회 시간 초과는 본문을 지우고 재조회만 허용한다', async () => {
  let finish;
  const {doc}=await setup(() => new Promise(resolve => {finish=resolve;}),1);
  doc.querySelector('#refresh').click(); await new Promise(resolve => setTimeout(resolve,10));
  assert.match(doc.querySelector('#notice').textContent,/지연되거나 실패/);
  assert.equal(doc.querySelectorAll('article').length,0);
  finish(snapshot()); await tick();
  assert.equal(doc.querySelectorAll('article').length,0);
  assert.equal(doc.querySelector('#refresh').disabled,false);
});
test('서버 오류의 원문은 화면에 표시하지 않는다', async () => {
  const {app,doc}=await setup(); app.ontoolresult({isError:true,content:[{text:'PRIVATE_ERROR'}]});
  assert.equal(doc.body.textContent.includes('PRIVATE_ERROR'),false);
  assert.equal(doc.querySelectorAll('article').length,0);
});
