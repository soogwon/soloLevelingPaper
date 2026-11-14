import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { mountAnnotation } from './annotation.js';

test('번역 구간의 출처를 원문으로 표시하지 않는다', () => {
  const doc = new JSDOM(readFileSync(new URL('./annotation-template.html', import.meta.url), 'utf8')).window.document;
  const app = {connect:async()=>{}};
  mountAnnotation(app, doc);
  app.ontoolresult({structuredContent:{annotation:{kind:'highlight',body:'',segments:[
    {position:0,target:'translation',quote_snapshot:'첫째.'}]},segment_available:[true],
    source:{position:0,pdf_page:1,before:'',quote:'첫째.',after:''}}});
  assert.equal(doc.querySelector('#location').textContent, 'PDF 1페이지 · 저장 번역문');
  assert.match(doc.querySelector('li').textContent, /번역문/);
});

test('클릭 시 원문을 다시 조회하고 바뀐 구간은 이동을 막는다', async () => {
  const doc = new JSDOM(readFileSync(new URL('./annotation-template.html', import.meta.url), 'utf8')).window.document;
  const annotation = {context_id:'ctx', annotation_id:'id', body:'메모', kind:'note',
    segments:[{position:0, quote_snapshot:'😀'}]};
  const detail = {annotation, segment_available:[true], source:null};
  let reply = {...detail, source:{position:0, pdf_page:3, before:'A',quote:'😀',after:'B'}};
  const app = {connect:async()=>{}, callServerTool:async ({arguments: args})=>{
    assert.deepEqual(args.request,{context_id:'ctx',annotation_id:'id',source_position:0});
    return {structuredContent:reply};
  }};
  mountAnnotation(app,doc);
  app.ontoolresult({structuredContent:detail});
  await doc.querySelector('li button').onclick();
  assert.equal(doc.querySelector('mark').textContent,'😀');
  assert.equal(doc.querySelector('blockquote').textContent,'A😀B');
  assert.equal(doc.querySelector('#location').textContent,'PDF 3페이지 · 원문');
  reply = {...detail,segment_available:[false]};
  await doc.querySelector('li button').onclick();
  assert.equal(doc.querySelector('li button'),null);
  assert.equal(doc.querySelector('mark'),null);
  assert.match(doc.querySelector('li').textContent,/찾을 수 없습니다/);
});
