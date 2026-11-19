import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';
import {mountRemoteReflection} from './remote-reflection.js';

const html = await readFile(new URL('./remote-reflection-template.html', import.meta.url), 'utf8');
const view = {
  context_id:'context',card_binding:'a'.repeat(64),question_revision:'revision',
  consent:{storage:'current',question_revision:'revision',consent_id:'consent'},
  questions:[{template_id:'before',phase:'before',prompt:'읽기 전 질문'},
    {template_id:'after',phase:'after',prompt:'읽기 후 질문'}],
  answers:[],groups:[{before_id:'before',after_ids:['after']}],
};
const result = value => ({structuredContent:{status:'ok',result:value}});

test('응답 유실은 같은 키를 쓰며 저장본만 비교한다', async () => {
  const doc = new JSDOM(html).window.document, calls=[];
  const app = {connect:async()=>{},callServerTool:async call=>{
    const args=call.arguments;
    calls.push(args);
    if(calls.length===1) throw Error();
    return result({...args,revision:1});
  }};
  mountRemoteReflection(app,doc,{randomUUID:()=>`key-${calls.length}`});
  app.ontoolinput({arguments:{context_id:'context'}});
  app.ontoolresult(result(view));
  doc.querySelector('textarea').value='합성 답변';
  const save=doc.querySelector('#answers button');
  assert.doesNotMatch(doc.getElementById('comparison').textContent,/합성 답변/);
  await save.onclick();
  await save.onclick();
  assert.deepEqual(calls[0],calls[1]);
  assert.match(doc.getElementById('comparison').textContent,/합성 답변/);
  assert.match(doc.getElementById('comparison').textContent,/저장된 답변 없음/);
});

test('동의 없이 저장을 막고 새 맥락에는 이전 응답을 표시하지 않는다', async () => {
  const doc=new JSDOM(html).window.document;
  let finish;
  const app={connect:async()=>{},callServerTool:()=>new Promise(resolve=>{finish=resolve;})};
  mountRemoteReflection(app,doc);
  app.ontoolinput({arguments:{context_id:'context'}});
  app.ontoolresult(result({...view,consent:{storage:'missing'}}));
  assert.equal(doc.querySelector('#answers button').disabled,true);
  const refresh=doc.getElementById('refresh').onclick();
  app.ontoolinput({arguments:{context_id:'other'}});
  finish(result(view));
  await refresh;
  assert.equal(doc.querySelector('textarea'),null);
});
