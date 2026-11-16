import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';
import {mountDeletion} from './server-deletion.js';
const template=readFileSync(new URL('./server-deletion-template.html',import.meta.url),'utf8');
function setup(sender){const dom=new JSDOM(template);const calls=[];const app={connect(){},async callServerTool(x){calls.push(x);return sender(x);}};mountDeletion(app,dom.window.document);return {app,d:dom.window.document,calls};}
const prepared={paper_id:'p',title:'합성 논문',notice:'삭제 고지',confirmation_token:'token'};
const result={structuredContent:{paper_id:'p',status:'blocking',backup_erasure_confirmed:false}};
const tick=()=>new Promise(r=>setImmediate(r));
test('명시적 확인 전 전송 없음, 접수는 완료로 표시하지 않음',async()=>{const {app,d,calls}=setup(()=>result);app.ontoolresult({structuredContent:{...prepared}});d.querySelector('#confirm').click();assert.equal(calls.length,0);d.querySelector('#agree').click();d.querySelector('#confirm').click();await tick();assert.equal(calls.length,1);assert.match(d.querySelector('#status').textContent,/정리 대기/);assert.equal(d.querySelector('#agree').checked,false);});
test('오류 후 같은 대상의 조회 및 재전송만 제공',async()=>{const {app,d,calls}=setup(()=>{throw Error('secret');});app.ontoolresult({structuredContent:{...prepared}});d.querySelector('#agree').click();d.querySelector('#confirm').click();await tick();assert.match(d.querySelector('#status').textContent,/미확정/);assert.equal(d.querySelector('#confirm').disabled,true);d.querySelector('#retry').click();await tick();assert.equal(calls[1].name,'retry_server_paper_deletion');assert.equal(calls[1].arguments.paper_id,'p');assert.doesNotMatch(d.body.textContent,/secret/);});
test('늦은 응답은 새 대상 화면을 덮지 않음',async()=>{let done;const {app,d}=setup(()=>new Promise(r=>done=r));app.ontoolresult({structuredContent:{...prepared}});d.querySelector('#agree').click();d.querySelector('#confirm').click();app.ontoolresult({structuredContent:{...prepared,paper_id:'new',title:'새 대상'}});done(result);await tick();assert.equal(d.querySelector('#title').textContent,'새 대상');assert.match(d.querySelector('#status').textContent,/범위/);});
test('complete를 백업 삭제 확인으로 과장하지 않음',()=>{const {app,d}=setup(()=>result);app.ontoolresult({structuredContent:{...result.structuredContent,status:'complete'}});assert.match(d.querySelector('#status').textContent,/백업 완전 삭제는 미확인/);});

test('호스트 취소 뒤 늦은 응답은 승인이나 상태를 복원하지 않음',async()=>{
  let done;const {app,d}=setup(()=>new Promise(r=>done=r));
  app.ontoolresult({structuredContent:{...prepared}});
  d.querySelector('#agree').click();d.querySelector('#confirm').click();
  app.ontoolcancelled();done(result);await tick();
  app.ontoolresult({structuredContent:{...prepared}});
  assert.equal(d.querySelector('#confirm').disabled,true);
  assert.equal(d.querySelector('#retry').disabled,true);
  assert.doesNotMatch(d.querySelector('#status').textContent,/정리 대기/);
});
