import test from 'node:test';
import assert from 'node:assert/strict';
const listeners=new Map();
globalThis.window={location:{origin:'https://example.test',search:''},addEventListener:(k,f)=>listeners.set(k,f),removeEventListener:k=>listeners.delete(k),dispatchEvent(){}};
globalThis.document={cookie:''};
const {remotePaperSummary}=await import('../js/lib/remote-paper-summary.js');
const id=n=>`00000000-0000-0000-0000-${String(n).padStart(12,'0')}`;
const paper=id(1),context=id(2),publication=id(3);
const contexts={items:[{context_id:context,paper_id:paper,server_version_id:'v1',publication_id:publication}],next_cursor:null};
const saved={state:'completed',context_id:context,request_key:id(4),result:{context_id:context,binding:{context_id:context,paper_id:paper,server_version_id:'v1',publication_id:publication},scope:'section',section_id:'s1',generated_at:'2026-11-19',items:[{kind:'method',text:'<script>합성 요약</script>',evidence_ids:[id(5)]}]}};
const response=data=>({ok:true,status:200,json:async()=>data});
function view(){const nodes=new Map();return {isConnected:true,innerHTML:'',textContent:'',querySelector(s){if(!nodes.has(s))nodes.set(s,{});return nodes.get(s);},querySelectorAll(){return [];}};}
test('현재 판본 GET 조회로 범위·출처·요약을 안전하게 표시한다',async t=>{
 const calls=[];t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push([String(url),opts.method]);return response(calls.length===1?contexts:saved);});
 const v=view();await remotePaperSummary(v,paper,'v1');assert.equal(calls.length,2);assert(calls.every(c=>c[1]==='GET'));assert.match(calls[0][0],/version_id=v1/);assert.match(v.innerHTML,/섹션 s1/);assert.match(v.innerHTML,/&lt;script&gt;합성 요약/);assert.match(v.innerHTML,/근거 ID/);listeners.get('pagehide')();
});
for(const event of ['auth:logout','auth:expired','hashchange','pagehide'])test(`늦은 요약 차단 ${event}`,async t=>{
 let resolve,reached;const ready=new Promise(r=>reached=r);
 t.mock.method(globalThis,'fetch',async url=>{if(!String(url).includes('/latest'))return response(contexts);reached();return new Promise(r=>resolve=r);});
 const v=view(),work=remotePaperSummary(v,paper,'v1');await ready;listeners.get(event)();const before=v.innerHTML;resolve(response(saved));await work;assert.equal(v.innerHTML,before);assert.equal(v.textContent,'');
});
test('다른 판본·게시본을 표시하지 않고 재조회 오류는 기존 내용을 지운다',async t=>{
 let count=0;t.mock.method(globalThis,'fetch',async()=>{if(++count>2)throw Error();return response(count===1?contexts:saved);});
 const v=view();await remotePaperSummary(v,paper,'v1');await v.querySelector('[data-summary-refresh]').onclick();assert.doesNotMatch(v.innerHTML,/합성 요약/);assert.match(v.innerHTML,/조회하지 못했습니다/);listeners.get('pagehide')();t.mock.restoreAll();
 for(const field of ['server_version_id','publication_id']){let n=0;const wrong=structuredClone(saved);wrong.result.binding[field]='other';t.mock.method(globalThis,'fetch',async()=>response(++n===1?contexts:wrong));const w=view();await remotePaperSummary(w,paper,'v1');assert.doesNotMatch(w.innerHTML,/합성 요약/);listeners.get('pagehide')();t.mock.restoreAll();}
});
test('요약 없음은 오류와 구분한다',async t=>{
 t.mock.method(globalThis,'fetch',async()=>response({items:[],next_cursor:null}));const v=view();await remotePaperSummary(v,paper,'v1');assert.match(v.innerHTML,/저장된 원격 요약이 없습니다/);listeners.get('pagehide')();
});
test('맥락 더 보기 커서와 새로고침을 유지한다',async t=>{
 const calls=[];const first={...contexts,next_cursor:context};
 t.mock.method(globalThis,'fetch',async url=>{const u=String(url);calls.push(u);if(u.includes('/latest'))return response(saved);if(u.includes('after='))return response({items:[{...contexts.items[0],context_id:id(9)}],next_cursor:null});return response(first);});
 const v=view();await remotePaperSummary(v,paper,'v1');await v.querySelector('[data-summary-more]').onclick();assert(calls.some(u=>u.includes(`after=${context}`)));assert.match(v.innerHTML,new RegExp(id(9)));
 await v.querySelector('[data-summary-refresh]').onclick();assert.match(v.innerHTML,/합성 요약/);assert.doesNotMatch(v.innerHTML,new RegExp(id(9)));listeners.get('pagehide')();
});
