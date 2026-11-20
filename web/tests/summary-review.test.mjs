import test from 'node:test';
import assert from 'node:assert/strict';
const listeners=new Map();
globalThis.window={location:{search:'',origin:'https://example.test'},addEventListener:(n,f)=>listeners.set(n,f),removeEventListener:n=>listeners.delete(n),dispatchEvent:()=>{}};
globalThis.document={cookie:'csrf_token=synthetic'};
const {default:page}=await import('../js/pages/summary-review.js');
const key='11111111-1111-4111-8111-111111111111';
const value={server_version_id:'<script>합성 판본</script>',publication_id:'게시',scope:'paper',section_id:null,source_count:1,provider:'synthetic',model:'synthetic',automatic_generation:false,policy:{max_calls:1,max_input_tokens:1000,max_output_tokens:100},expires_at:'2099',storage:'missing',ai_provider:'missing',active:true,notice_version:'remote-summary-v1',storage_notice:'저장',ai_notice:'전송'};
function view(){const nodes=new Map();return {isConnected:true,innerHTML:'',textContent:'',querySelector(s){if(!nodes.has(s))nodes.set(s,{dataset:{},disabled:false});return nodes.get(s);},querySelectorAll(){return [];}};}
test('조회만으로 동의와 접수가 발생하지 않고 본문은 이스케이프된다',async t=>{
 const calls=[];t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push([String(url),opts]);return {ok:true,status:200,json:async()=>value};});
 const v=view();await page({view:v,params:{requestKey:key}});
 assert.equal(calls.length,1);assert.equal(calls[0][0],`https://example.test/v1/remote-summaries/reviews/${key}`);assert.equal(calls[0][1].method,'GET');
 assert.match(v.innerHTML,/&lt;script&gt;/);assert.doesNotMatch(v.innerHTML,/<script>/);assert.match(v.innerHTML,/data-submit disabled/);
 listeners.get('auth:logout')();
});
test('로그아웃 후 늦은 응답이 이전 요약을 표시하지 않는다',async t=>{
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

test('기존 결과 조회는 GET만 수행하고 요약을 이스케이프한다',async t=>{
 const calls=[];t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push(opts.method);return {ok:true,status:200,json:async()=>String(url).endsWith('/result')?{state:'completed',execution_id:'same',result:{generated_at:'now',items:[{kind:'claim',text:'<script>요약</script>',evidence_ids:['e1']}]}}:{...value,state:'completed',execution_id:'same'}};});
 const v=view();await page({view:v,params:{requestKey:key}});await v.querySelector('[data-result]').onclick();
 assert.deepEqual(calls,['GET','GET']);assert.match(v.innerHTML,/&lt;script&gt;요약/);assert.match(v.innerHTML,/근거: e1/);listeners.get('pagehide')();
});
test('상한 불일치면 접수를 막고 취소와 조회는 유지한다',async t=>{
 const calls=[];t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push([String(url),opts.method]);return {ok:true,status:200,json:async()=>({...value,policy:{...value.policy,max_calls:2},state:'queued',execution_id:'same',storage:'current',ai_provider:'current'})};});
 const v=view();await page({view:v,params:{requestKey:key}});await v.querySelector('[data-submit]').onclick();assert.equal(calls.length,1);
 await v.querySelector('[data-cancel]').onclick();assert.match(calls[1][0],/\/cancel$/);assert.equal(calls[1][1],'POST');listeners.get('pagehide')();
});

test('두 동의가 있어도 예산 부족이면 접수 오류를 구분하고 한 번만 제출한다', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push(options.method);
    if (options.method === 'POST') return { ok: false, status: 409, headers: new Headers(),
      json: async () => ({ detail: { code: 'summary_budget_unavailable' } }) };
    return { ok: true, status: 200, json: async () => ({ ...value, storage: 'current', ai_provider: 'current' }) };
  });
  const v = view();
  await page({ view: v, params: { requestKey: key } });
  await v.querySelector('[data-submit]').onclick();
  assert.match(v.innerHTML, /계정 또는 서비스의 예산/);
  assert.deepEqual(calls, ['GET', 'POST']);
  listeners.get('pagehide')();
});
