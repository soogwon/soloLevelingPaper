import test from 'node:test';
import assert from 'node:assert/strict';
const listeners = new Map();
globalThis.window = {location:{search:'',origin:'https://example.test'},addEventListener:(n,f)=>listeners.set(n,f),removeEventListener:n=>listeners.delete(n),dispatchEvent:()=>{}};
globalThis.document = {cookie:'csrf_token=synthetic'};
const {default:page} = await import('../js/pages/reprocessing-review.js');
const key = '11111111-1111-4111-8111-111111111111';
const context = '22222222-2222-4222-8222-222222222222';
const value = {snapshot:{preview:{context_id:context,operation:'reparse',version_id:'<script>원문</script>',publication_id:'p',target_count:1}},contract:{notice_version:'remote-reprocessing-v1',execution_enabled:false,token_limit_scope:'entire_job',max_cost_usd:'0.006',max_calls:2,max_input_tokens:4000,max_output_tokens:1000,provider:'synthetic',model:'synthetic'},state:'review',active:true,storage:'current',ai_provider:'current',request_key:key};
function view(){const nodes=new Map();return {isConnected:true,innerHTML:'',textContent:'',querySelector(s){if(!nodes.has(s))nodes.set(s,{dataset:{},disabled:false});return nodes.get(s);},querySelectorAll(){return [];}};}
const args = v => ({view:v,params:{contextId:context,operation:'reparse'},query:{key}});
test('최초 조회는 GET만 수행하고 비용·대기 안내·이스케이프를 표시한다',async t=>{
 const calls=[];t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push(opts.method);return {ok:true,status:200,json:async()=>value};});
 const v=view();await page(args(v));assert.deepEqual(calls,['GET']);assert.match(v.innerHTML,/0.006/);assert.match(v.innerHTML,/접수만으로 실행하지/);assert.match(v.innerHTML,/&lt;script&gt;/);listeners.get('pagehide')();
});
test('로그아웃 후 늦은 응답은 표시하지 않는다',async t=>{
 let resolve;t.mock.method(globalThis,'fetch',()=>new Promise(r=>{resolve=r;}));const v=view();const pending=page(args(v));listeners.get('auth:logout')();const before=v.innerHTML;resolve({ok:true,status:200,json:async()=>value});await pending;assert.equal(v.innerHTML,before);assert.equal(v.textContent,'');
});
test('접수 응답 유실 후 같은 키 조회로 복원하며 자동 재접수하지 않는다',async t=>{
 let n=0;const calls=[];t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push([String(url),opts.method]);if(++n===2)throw Error('유실');return {ok:true,status:200,json:async()=>n>2?{...value,state:'queued',job_id:'same-job',active:false}:value};});
 const v=view();await page(args(v));await v.querySelector('[data-submit]').onclick();await v.querySelector('[data-refresh]').onclick();assert.deepEqual(calls.map(c=>c[1]),['GET','POST','GET']);assert.equal(calls[0][0],calls[2][0]);assert.match(v.innerHTML,/same-job/);assert.match(v.innerHTML,/실행 승인 대기/);listeners.get('pagehide')();
});
test('계약 불일치는 접수를 차단한다',async t=>{
 const calls=[];t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push(opts.method);return {ok:true,status:200,json:async()=>({...value,contract:{...value.contract,max_cost_usd:null}})};});
 const v=view();await page(args(v));await v.querySelector('[data-submit]').onclick();assert.deepEqual(calls,['GET']);listeners.get('pagehide')();
});
test('미저장 키는 읽기 미리보기만 열고 명시적 검토 저장 버튼에서 POST한다',async t=>{
 const calls=[];t.mock.method(globalThis,'fetch',async(url,opts)=>{
   calls.push(opts.method);
   if(calls.length===1)return {ok:false,status:404,headers:{get:()=>null},json:async()=>({detail:{code:'reprocessing_not_found'}})};
   return {ok:true,status:200,json:async()=>calls.length===2?{...value,state:'draft',request_key:null,storage:'missing',ai_provider:'missing'}:value};
 });
 const v=view();await page(args(v));assert.deepEqual(calls,['GET','GET']);await v.querySelector('[data-prepare]').onclick();assert.deepEqual(calls,['GET','GET','POST']);listeners.get('pagehide')();
});
test('저장된 worker 결과는 게시 완료로 표시하거나 다시 접수하지 않는다',async t=>{
 const calls=[];t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push(opts.method);return {ok:true,status:200,json:async()=>({...value,state:'staged',active:false,job_id:'same-job',result_ready:true,publication_ready:false})};});
 const v=view();await page(args(v));await v.querySelector('[data-submit]').onclick();await v.querySelector('[data-cancel]').onclick();assert.deepEqual(calls,['GET']);assert.match(v.innerHTML,/결과 저장됨·새 색인 게시 대기/);listeners.get('pagehide')();
});
