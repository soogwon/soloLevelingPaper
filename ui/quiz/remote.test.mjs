import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';
import {mountRemoteQuiz} from './remote.js';
const ctx='00000000-0000-0000-0000-000000000001',quiz='00000000-0000-0000-0000-000000000002',key='00000000-0000-0000-0000-000000000003',aid='00000000-0000-0000-0000-000000000004';
const scope={context_id:ctx,quiz_id:quiz,binding:'a'.repeat(64),consent:{storage:'current',consent_id:'consent'},quiz:{context_id:ctx,quiz_id:quiz,questions:[{position:0,question_type:'multiple_choice',prompt:'<img src=x onerror=alert(1)>',choices:['첫째','둘째']}]}};
const attempt={context_id:ctx,quiz_id:quiz,request_key:key,attempt_id:aid,correct_count:0,total_count:1,results:[{position:0,prompt:'합성 문항',is_correct:false,correct_answer:'첫째',grading_criteria:'정확 일치',evidence_ids:['fixed-evidence']}]};
const response=v=>({structuredContent:{status:'ok',result:v}});
function setup(call=async()=>response({...scope,request_key:key,attempt})){
 const dom=new JSDOM(readFileSync(new URL('./remote-template.html',import.meta.url),'utf8'));const doc=dom.window.document;
 const calls=[];const app={connect:async()=>{},callServerTool:args=>{calls.push(args);return call(args);}};
 mountRemoteQuiz(app,doc,{randomUUID:()=>key});
 app.ontoolinput({arguments:{context_id:ctx,quiz_id:quiz}});app.ontoolresult(response(structuredClone(scope)));
 const el=id=>doc.getElementById(id);
 const submit=()=>{el('answer-0').value='1';el('confirm').checked=true;el('confirm').onchange();return el('submit').onclick();};
 return {app,doc,el,calls,submit};
}
test('명시적 제출 후 점수와 오답 근거, 원문 이스케이프',async()=>{const x=setup();assert.equal(x.calls.length,0);assert.equal(x.doc.querySelector('img'),null);await x.submit();assert.equal(x.calls.length,1);assert.equal(x.calls[0].arguments.request_key,key);assert.deepEqual(x.calls[0].arguments.answers,{'0':1});assert.match(x.el('review').textContent,/fixed-evidence/);await x.el('submit').onclick();assert.equal(x.calls.length,1);});
test('응답 유실 뒤 같은 키 읽기만 수행',async()=>{const x=setup(async args=>{if(args.name==='submit_remote_quiz_card')throw Error();return response({...scope,request_key:key,attempt});});await x.submit();await x.el('lookup').onclick();assert.deepEqual(x.calls.map(v=>v.name),['submit_remote_quiz_card','get_remote_quiz_submission']);assert.equal(x.calls[1].arguments.request_key,key);assert.match(x.el('review').textContent,/정답 0\/1/);});
for(const stop of ['input','cancel','pagehide'])test(`${stop} 후 늦은 응답 차단`,async()=>{let resolve;const x=setup(()=>new Promise(r=>resolve=r));const pending=x.submit();if(stop==='input')x.app.ontoolinput({arguments:{context_id:ctx,quiz_id:quiz}});if(stop==='cancel')x.app.ontoolcancelled();if(stop==='pagehide')x.doc.defaultView.dispatchEvent(new x.doc.defaultView.Event('pagehide'));resolve(response({...scope,request_key:key,attempt}));await pending;assert.equal(x.el('review').textContent,'');assert.equal(x.el('lookup').disabled,true);});
test('다른 연결 결과는 표시하지 않음',async()=>{const x=setup(async()=>response({...scope,binding:'b'.repeat(64),request_key:key,attempt}));await x.submit();assert.equal(x.el('review').textContent,'');});
test('철회된 저장 동의에서 제출 차단',async()=>{const x=setup();x.app.ontoolinput({arguments:{context_id:ctx,quiz_id:quiz}});x.app.ontoolresult(response({...scope,consent:{storage:'missing'}}));await x.submit();assert.equal(x.calls.length,0);});
test('저장 복습 카드는 새 제출 없이 복원',()=>{const x=setup();x.app.ontoolinput({arguments:{context_id:ctx,attempt_id:aid}});x.app.ontoolresult(response({...scope,request_key:key,attempt}));assert.match(x.el('review').textContent,/정답 0\/1/);assert.equal(x.calls.length,0);assert.equal(x.el('submit').disabled,true);});
test('미확인 결과는 자동 재제출 없음',async()=>{const x=setup(async()=>response({...scope,request_key:key,attempt:null}));await x.submit();await x.el('lookup').onclick();assert.equal(x.calls.filter(v=>v.name==='submit_remote_quiz_card').length,1);assert.match(x.el('status').textContent,/아직 없습니다/);});

test('저장 시도에서 점수와 고정 오답 원문을 읽기 전용으로 연다',async()=>{
 const x=setup(async args=>args.name==='get_remote_quiz_card_review'
   ? response({...scope,attempt_id:aid,scores:[{step:'review',attempts:2,latest_score:.5,best_score:1}],
     evidence:[{evidence_id:'fixed-evidence',pdf_page:1,quote_original:'합성 원문'}],evidence_count:1})
   : response({...scope,request_key:key,attempt}));
 assert.equal(x.el('details').hidden,true);
 await x.submit();
 await x.el('details').onclick();
 assert.deepEqual(x.calls.map(c=>c.name),['submit_remote_quiz_card','get_remote_quiz_card_review']);
 assert.equal(x.calls[1].arguments.attempt_id,aid);
 assert.match(x.el('details-output').textContent,/최근 50%/);
 assert.match(x.el('details-output').textContent,/합성 원문/);
});
