import test from 'node:test';
import assert from 'node:assert/strict';
const listeners=new Map();
globalThis.window={location:{search:'',origin:'https://example.test'},addEventListener:(n,f)=>listeners.set(n,f),removeEventListener:n=>listeners.delete(n),dispatchEvent:()=>{}};
globalThis.document={cookie:'csrf_token=synthetic'};
const {default:page}=await import('../js/pages/quiz-consent.js');
const context='11111111-1111-4111-8111-111111111111';
const quiz='22222222-2222-4222-8222-222222222222';
const value={context_id:context,quiz_id:quiz,notice_version:'remote-quiz-attempt-v1',quiz_revision:'a'.repeat(64),storage:'missing',notice:'<script>합성 고지</script>'};
function view(){const nodes=new Map();return {isConnected:true,innerHTML:'',textContent:'',querySelector(s){if(!nodes.has(s))nodes.set(s,{disabled:false,checked:false});return nodes.get(s);}};}
function response(value){return {ok:true,status:200,json:async()=>value};}
test('최초 조회와 체크 없는 클릭은 동의를 기록하지 않고 고지를 이스케이프한다',async t=>{
 const calls=[];t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push(opts.method);return response(value);});
 const v=view();await page({view:v,params:{contextId:context,quizId:quiz}});await v.querySelector('[data-grant]').onclick();
 assert.deepEqual(calls,['GET']);assert.match(v.innerHTML,/&lt;script&gt;/);assert.doesNotMatch(v.innerHTML,/<script>/);listeners.get('pagehide')();
});
test('명시 선택만 고지·질문 revision으로 저장하고 철회는 별도 요청한다',async t=>{
 const calls=[];t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push([String(url),opts]);return response({...value,storage:opts.method==='POST'&&!String(url).endsWith('/revoke')?'current':'missing'});});
 const v=view();await page({view:v,params:{contextId:context,quizId:quiz}});
 const c=v.querySelector('[data-check]');c.checked=true;c.onchange();await v.querySelector('[data-grant]').onclick();
 assert.deepEqual(JSON.parse(calls[1][1].body),{notice_version:value.notice_version,quiz_revision:value.quiz_revision});
 await v.querySelector('[data-revoke]').onclick();assert.match(calls[2][0],/\/consent\/revoke$/);listeners.get('pagehide')();
});
test('응답 유실 후 자동 재동의 없이 GET으로 현재 상태를 복구한다',async t=>{
 const calls=[];t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push(opts.method);if(calls.length===2)throw Error('유실');return response({...value,storage:calls.length>2?'current':'missing'});});
 const v=view();await page({view:v,params:{contextId:context,quizId:quiz}});const c=v.querySelector('[data-check]');c.checked=true;c.onchange();await v.querySelector('[data-grant]').onclick();await v.querySelector('[data-grant]').onclick();await v.querySelector('[data-refresh]').onclick();
 assert.deepEqual(calls,['GET','POST','GET']);assert.match(v.innerHTML,/동의됨/);listeners.get('pagehide')();
});
test('로그아웃 뒤 늦은 동의 응답을 표시하지 않는다',async t=>{
 let resolve;let count=0;t.mock.method(globalThis,'fetch',async()=>++count===1?response(value):new Promise(r=>{resolve=r;}));
 const v=view();await page({view:v,params:{contextId:context,quizId:quiz}});const c=v.querySelector('[data-check]');c.checked=true;c.onchange();const pending=v.querySelector('[data-grant]').onclick();listeners.get('auth:logout')();const before=v.innerHTML;resolve(response({...value,storage:'current'}));await pending;
 assert.equal(v.innerHTML,before);assert.equal(v.textContent,'');
});
test('다른 맥락 또는 고지 응답은 저장 버튼을 활성화하지 않는다',async t=>{
 let count=0;t.mock.method(globalThis,'fetch',async()=>{count++;return response({...value,context_id:'다른 맥락'});});const v=view();await page({view:v,params:{contextId:context,quizId:quiz}});await v.querySelector('[data-grant]').onclick();assert.equal(count,1);assert.match(v.innerHTML,/상태를 확인하지 못/);listeners.get('pagehide')();
});
