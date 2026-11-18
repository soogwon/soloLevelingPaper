import test from 'node:test';
import assert from 'node:assert/strict';
const listeners=new Map();
globalThis.window={location:{search:'',origin:'https://example.test'},addEventListener:(n,f)=>listeners.set(n,f),removeEventListener:n=>listeners.delete(n),dispatchEvent:()=>{}};
globalThis.document={cookie:'csrf_token=synthetic'};
const {default:page}=await import('../js/pages/question-review.js');
const key='11111111-1111-4111-8111-111111111111';
const value={question:'<script>합성 질문</script>',version_id:'판본',pages:[6],sections:[],strict_pages:false,top_k:1,models:{generation_model:'synthetic',semantic_model:'disabled'},transfer:{input_count_max_calls:0,generation_max_calls:1,total_max_calls:1},policy:{max_calls:1,max_input_tokens:1000,max_output_tokens:100},expires_at:'2099',storage:'missing',ai_provider:'missing',active:true,notice_version:'remote-question-v1',storage_notice:'저장',ai_notice:'전송'};
function view(){const nodes=new Map();return {isConnected:true,innerHTML:'',textContent:'',querySelector(s){if(!nodes.has(s))nodes.set(s,{dataset:{},disabled:false});return nodes.get(s);},querySelectorAll(){return [];}};}
test('조회만으로 동의와 접수가 발생하지 않고 본문은 이스케이프된다',async t=>{
 const calls=[];t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push([String(url),opts]);return {ok:true,status:200,json:async()=>value};});
 const v=view();await page({view:v,params:{requestKey:key}});
 assert.equal(calls.length,1);assert.equal(calls[0][0],`https://example.test/v1/remote-questions/reviews/${key}`);assert.equal(calls[0][1].method,'GET');
 assert.match(v.innerHTML,/&lt;script&gt;/);assert.doesNotMatch(v.innerHTML,/<script>/);assert.match(v.innerHTML,/data-submit disabled/);
 listeners.get('auth:logout')();
});
test('로그아웃 후 늦은 응답이 이전 질문을 표시하지 않는다',async t=>{
 let resolve; t.mock.method(globalThis,'fetch',()=>new Promise(r=>{resolve=r;}));
 const v=view();const pending=page({view:v,params:{requestKey:key}});listeners.get('auth:logout')();
 const after=v.innerHTML;resolve({ok:true,status:200,json:async()=>value});await pending;
 assert.equal(v.innerHTML,after);assert.equal(v.textContent,'');
});
test('접수 응답 유실 후 조회는 같은 키로 실행 ID를 복원하고 재제출하지 않는다',async t=>{
 let count=0;const calls=[];t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push(opts.method);count++;if(count===2)throw Error('유실');return {ok:true,status:200,json:async()=>({...value,storage:'current',ai_provider:'current',state:count>2?'queued':null,execution_id:count>2?'same-execution':null})};});
 const v=view();await page({view:v,params:{requestKey:key}});await v.querySelector('[data-submit]').onclick();await v.querySelector('[data-refresh]').onclick();
 assert.deepEqual(calls,['GET','POST','GET']);assert.match(v.innerHTML,/same-execution/);listeners.get('pagehide')();
});

test('접수 성공 직후 상태 요약과 실행 ID가 함께 갱신된다',async t=>{
 t.mock.method(globalThis,'fetch',async(url,opts)=>({ok:true,status:200,json:async()=>opts.method==='POST'?{state:'queued',execution_id:'accepted-id'}:{...value,storage:'current',ai_provider:'current'}}));
 const v=view();await page({view:v,params:{requestKey:key}});await v.querySelector('[data-submit]').onclick();
 assert.match(v.innerHTML,/실행 상태: queued · 실행 ID: accepted-id/);
 assert.doesNotMatch(v.innerHTML,/실행 상태: 미접수/);listeners.get('pagehide')();
});

for (const invalid of [false, true]) test(`계수 고지의 두 전송 표시와 불일치 차단 ${invalid}`, async t => {
 const calls=[];
 const data={...value,notice_version:'question-count-and-generation-v1',storage:'current',ai_provider:'current',
  transfer:{input_count_max_calls:1,generation_max_calls:1,total_max_calls:invalid?1:2}};
 t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push(opts.method);return {ok:true,status:200,json:async()=>data};});
 const v=view();await page({view:v,params:{requestKey:key}});
 if(invalid){assert.match(v.innerHTML,/data-submit disabled/);await v.querySelector('[data-submit]').onclick();assert.deepEqual(calls,['GET']);}
 else {assert.match(v.innerHTML,/입력 토큰 계수 최대 1회/);assert.match(v.innerHTML,/외부 전송 합계 최대 2회/);}
 listeners.get('pagehide')();
});

test('새 고지를 확인한 동의 저장에는 해당 고지 버전만 전송한다',async t=>{
 const calls=[];const data={...value,notice_version:'question-count-and-generation-v1',
  transfer:{input_count_max_calls:1,generation_max_calls:1,total_max_calls:2}};
 t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push(opts);return {ok:true,status:200,json:async()=>data};});
 const v=view(), grant={dataset:{grant:'ai_provider'}};
 v.querySelectorAll=s=>s==='[data-grant]'?[grant]:[];
 await page({view:v,params:{requestKey:key}});await grant.onclick();
 assert.deepEqual(JSON.parse(calls[1].body),{notice_version:'question-count-and-generation-v1'});
 listeners.get('pagehide')();
});


test('전송 횟수 누락 시 동의·접수는 차단하고 조회와 철회는 유지한다',async t=>{
 const calls=[];const data={...value,transfer:null,storage:'current',ai_provider:'current'};
 t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push([String(url),opts.method]);return {ok:true,status:200,json:async()=>data};});
 const v=view(), grant={dataset:{grant:'ai_provider'}}, revoke={dataset:{revoke:'ai_provider'}};
 v.querySelectorAll=s=>s==='[data-grant]'?[grant]:s==='[data-revoke]'?[revoke]:[];
 await page({view:v,params:{requestKey:key}});await grant.onclick();await v.querySelector('[data-submit]').onclick();
 assert.equal(calls.length,1);assert.match(v.innerHTML,/외부 전송 횟수를 확인할 수 없습니다/);
 await revoke.onclick();await v.querySelector('[data-refresh]').onclick();
 assert.deepEqual(calls.map(x=>x[1]),['GET','POST','GET']);assert.match(calls[1][0],/ai_provider\/revoke$/);
 listeners.get('pagehide')();
});
