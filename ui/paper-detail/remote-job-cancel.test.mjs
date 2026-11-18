import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {mountRemoteJobCancel} from './remote-job-cancel.js';
const job='11111111-1111-4111-8111-111111111111', key='22222222-2222-4222-8222-222222222222';
const reply={structuredContent:{status:'ok',result:{job_id:job,request_key:key,job:{job_id:job,status:'processing',title:'합성'},expected_revision:2,binding:'a'.repeat(64),can_cancel:true,notice:'이미 발생한 비용은 되돌리지 않습니다.'}}};
function setup(call) {
 const doc=new JSDOM('<p id="status"></p><pre id="record"></pre><button id="confirm" disabled></button><button id="refresh" disabled></button>').window.document;
 const app={connect:async()=>{},callServerTool:call}; mountRemoteJobCancel(app,doc);
 app.ontoolinput({arguments:{job_id:job,request_key:key}});app.ontoolresult(reply);return {app,doc};
}
test('명시적 클릭 후 한 번만 취소하고 응답 유실은 조회로 복구',async()=>{
 const calls=[];const {doc}=setup(async r=>{calls.push(r);if(r.name==='confirm_remote_job_cancellation')throw Error('lost');return {structuredContent:{status:'ok',result:{job_id:job,status:'cancelled',revision:3,applied_revision:3}}};});
 assert.equal(calls.length,0);await doc.getElementById('confirm').onclick();
 assert.equal(calls.length,1);assert.equal(calls[0].arguments.confirmed,true);
 await doc.getElementById('confirm').onclick();assert.equal(calls.length,1);
 await doc.getElementById('refresh').onclick();assert.equal(calls[1].name,'get_remote_job_cancellation');assert.equal(calls[1].arguments.request_key,key);
 assert.match(doc.getElementById('status').textContent,/저장됐습니다/);
});
test('새 입력과 취소 후 늦게 온 응답은 이전 대상을 복원하지 않는다',async()=>{
 let resolve;const {app,doc}=setup(()=>new Promise(r=>{resolve=r;}));const pending=doc.getElementById('confirm').onclick();
 app.ontoolinput({arguments:{job_id:'33333333-3333-4333-8333-333333333333',request_key:key}});
 resolve({structuredContent:{status:'ok',result:{job_id:job,status:'cancelled',revision:3,applied_revision:3}}});await pending;
 assert.equal(doc.getElementById('record').textContent,'');assert.equal(doc.getElementById('confirm').disabled,true);
 app.ontoolcancelled();app.ontoolresult(reply);assert.equal(doc.getElementById('confirm').disabled,true);
});

test('완료 작업은 확인 불가이며 다른 작업 응답과 잘못된 revision은 성공으로 표시하지 않는다',async()=>{
 const {app,doc}=setup(async()=>({structuredContent:{status:'ok',result:{job_id:job,status:'cancelled',revision:0,applied_revision:0}}}));
 await doc.getElementById('confirm').onclick();
 assert.doesNotMatch(doc.getElementById('status').textContent,/저장됐습니다/);
 app.ontoolinput({arguments:{job_id:job,request_key:key}});
 app.ontoolresult({structuredContent:{status:'ok',result:{...reply.structuredContent.result,job:{job_id:job,status:'completed'}}}});
 assert.equal(doc.getElementById('confirm').disabled,true);
 app.ontoolinput({arguments:{job_id:job,request_key:key}});
 app.ontoolresult({structuredContent:{status:'ok',result:{...reply.structuredContent.result,job_id:key}}});
 assert.equal(doc.getElementById('record').textContent,'');
 assert.equal(doc.getElementById('refresh').disabled,true);
});
test('페이지 종료 후 늦은 응답은 표시하지 않는다',async()=>{
 let resolve; const {doc}=setup(()=>new Promise(r=>{resolve=r;}));
 const pending=doc.getElementById('confirm').onclick();
 doc.defaultView.dispatchEvent(new doc.defaultView.Event('pagehide'));
 resolve({structuredContent:{status:'ok',result:{job_id:job,status:'cancelled',revision:3,applied_revision:3}}});
 await pending;
 assert.equal(doc.getElementById('record').textContent,'');
 assert.equal(doc.getElementById('refresh').disabled,true);
});
