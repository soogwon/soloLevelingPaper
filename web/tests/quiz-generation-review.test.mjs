import test from 'node:test';
import assert from 'node:assert/strict';
const listeners = new Map();
globalThis.window = { location:{search:'',origin:'https://example.test'}, addEventListener:(n,f)=>listeners.set(n,f), removeEventListener:n=>listeners.delete(n), dispatchEvent:()=>{} };
globalThis.document = {cookie:'csrf_token=synthetic'};
const {default:page} = await import('../js/pages/quiz-generation-review.js');
const key='11111111-1111-4111-8111-111111111111';
const value={request_key:key,input_hash:'remote-quiz-generation-v1:'+ 'a'.repeat(64),server_version_id:'<script>합성 판본</script>',publication_id:'게시',step:'preview',question_type:'multiple_choice',question_count:1,source_count:1,provider:'synthetic',model:'synthetic',automatic_generation:false,max_calls:1,max_input_tokens:1000,max_output_tokens:100,policy:{max_calls:1,max_input_tokens:1000,max_output_tokens:100},expires_at:'2026-01-01T00:01:00Z',server_now:'2026-01-01T00:00:00Z',storage:'missing',ai_provider:'missing',active:true,notice_version:'remote-quiz-generation-v1',storage_notice:'저장',ai_notice:'전송'};
function view(){
 const nodes=new Map();
 function node(s){if(!nodes.has(s)){const m=s.match(/\[data-(check|grant|revoke)="([^"]+)"\]/);nodes.set(s,{dataset:m?{[m[1]]:m[2]}:{},disabled:false,checked:false});}return nodes.get(s);}
 return {isConnected:true,innerHTML:'',textContent:'',querySelector:node,querySelectorAll:s=>['storage','ai_provider'].map(p=>node(s.replace(']',`="${p}"]`)))};
}
function reply(data){return {ok:true,status:200,json:async()=>data};}
function setup(t,handler){const calls=[];t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push({url:String(url),...opts});return handler?handler(String(url),opts,calls.length):reply(value);});t.after(()=>listeners.get('pagehide')?.());return calls;}
test('조회만 하고 저장·전송 동의는 별도로 표시하며 본문을 이스케이프한다',async t=>{
 const calls=setup(t);const v=view();await page({view:v,params:{requestKey:key}});
 assert.equal(calls.length,1);assert.equal(calls[0].method,'GET');assert.match(calls[0].url,/remote-quiz-generation\/reviews/);
 assert.match(v.innerHTML,/퀴즈·정답 저장/);assert.match(v.innerHTML,/AI 제공자 전송/);assert.match(v.innerHTML,/답안 제출 저장 동의는 별도/);assert.match(v.innerHTML,/data-submit disabled/);assert.match(v.innerHTML,/&lt;script&gt;/);
 await v.querySelector('[data-grant="storage"]').onclick();assert.equal(calls.length,1);
 const c=v.querySelector('[data-check="storage"]');c.checked=true;c.onchange();await v.querySelector('[data-grant="storage"]').onclick();
 assert.equal(calls[1].method,'POST');assert.match(calls[1].url,/consents\/storage$/);assert.deepEqual(JSON.parse(calls[1].body),{notice_version:value.notice_version,input_hash:value.input_hash});
});
test('같은 요청의 접수 응답 유실을 GET으로 복구하고 다시 제출하지 않는다',async t=>{
 const calls=setup(t,(url,opts,n)=>{if(n===2)throw Error('유실');return reply({...value,storage:'current',ai_provider:'current',state:n>2?'queued':null,execution_id:n>2?'same-execution':null});});
 const v=view();await page({view:v,params:{requestKey:key}});await v.querySelector('[data-submit]').onclick();await v.querySelector('[data-submit]').onclick();await v.querySelector('[data-refresh]').onclick();await v.querySelector('[data-submit]').onclick();
 assert.deepEqual(calls.map(c=>c.method),['GET','POST','GET']);assert.match(v.innerHTML,/same-execution/);
});
for(const event of ['auth:expired','auth:logout','hashchange','pagehide'])test(`${event} 이후 늦은 응답은 표시하지 않는다`,async t=>{
 let resolve;setup(t,()=>new Promise(r=>{resolve=r;}));const v=view();const pending=page({view:v,params:{requestKey:key}});listeners.get(event)();const before=v.innerHTML;resolve(reply(value));await pending;assert.equal(v.innerHTML,before);assert.equal(v.textContent,'');
});
test('열린 화면의 기한이 지나면 동의·접수를 막고 철회·취소는 유지한다',async t=>{
 let now=0;t.mock.method(performance,'now',()=>now);
 const calls=setup(t,()=>reply({...value,storage:'current',ai_provider:'current',state:'queued',execution_id:'same'}));
 const v=view();await page({view:v,params:{requestKey:key}});now=61000;await v.querySelector('[data-submit]').onclick();assert.equal(calls.length,1);
 await v.querySelector('[data-revoke="storage"]').onclick();assert.match(calls[1].url,/\/revoke$/);
 now=122000;await v.querySelector('[data-cancel]').onclick();assert.match(calls[2].url,/\/cancel$/);
});
test('클릭 직전 만료된 새 요청은 접수하지 않는다',async t=>{
 let now=0;t.mock.method(performance,'now',()=>now);const calls=setup(t,()=>reply({...value,storage:'current',ai_provider:'current'}));
 const v=view();await page({view:v,params:{requestKey:key}});now=61000;await v.querySelector('[data-submit]').onclick();assert.equal(calls.length,1);
});
for(const change of [{active:false},{max_input_tokens:999},{policy:{...value.policy,max_calls:2}},{expires_at:'invalid'},{request_key:'foreign'},{question_count:0},{automatic_generation:true}])test(`검토 불일치 차단 ${JSON.stringify(change)}`,async t=>{
 const calls=setup(t,()=>reply({...value,storage:'current',ai_provider:'current',...change}));const v=view();await page({view:v,params:{requestKey:key}});await v.querySelector('[data-submit]').onclick();assert.equal(calls.length,1);assert.match(v.innerHTML,/data-submit disabled/);
});
test('동의 요청이 실패해도 자동 재저장하지 않고 체크를 다시 요구한다',async t=>{
 const calls=setup(t,(url,opts,n)=>{if(n===2)throw Error('유실');return reply(value);});const v=view();await page({view:v,params:{requestKey:key}});
 const c=v.querySelector('[data-check="ai_provider"]');c.checked=true;c.onchange();await v.querySelector('[data-grant="ai_provider"]').onclick();await v.querySelector('[data-grant="ai_provider"]').onclick();assert.equal(calls.length,2);
 await v.querySelector('[data-refresh]').onclick();await v.querySelector('[data-grant="ai_provider"]').onclick();assert.equal(calls.length,3);
});
test('기한 타이머는 만료 안내와 비활성 버튼을 표시한다',async t=>{
 let now=0,expire;t.mock.method(performance,'now',()=>now);t.mock.method(globalThis,'setTimeout',fn=>{expire=fn;return 0;});
 setup(t,()=>reply({...value,storage:'current',ai_provider:'current'}));const v=view();await page({view:v,params:{requestKey:key}});
 now=61000;expire();assert.match(v.innerHTML,/검토 기한이 지났습니다/);assert.match(v.innerHTML,/data-submit disabled/);
});
