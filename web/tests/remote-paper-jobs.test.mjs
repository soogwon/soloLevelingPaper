import test from 'node:test';
import assert from 'node:assert/strict';
const listeners = new Map();
globalThis.window = { location: { origin: 'https://example.test', search: '' }, addEventListener: (k,f) => listeners.set(k,f), removeEventListener: k => listeners.delete(k), dispatchEvent() {} };
globalThis.document = { cookie: '' };
const { remotePaperJobs } = await import('../js/lib/remote-paper-jobs.js');
const id = n => `00000000-0000-0000-0000-${String(n).padStart(12,'0')}`;
const paper=id(1), version='v1';
const job={job_id:id(3),paper_id:paper,server_version_id:version,status:'cancelled',revision:2,title:'<script>합성</script>'};
const response = data => ({ok:true,status:200,json:async()=>data});
function view() { const nodes=new Map();return {isConnected:true,innerHTML:'',textContent:'',querySelector(s){if(!nodes.has(s))nodes.set(s,{});return nodes.get(s);}}; }
test('GET 조회로 취소 상태를 학습 상태와 구분하고 더 보기와 새로고침을 제공한다',async t=>{
 const calls=[];
 t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push([String(url),opts.method]);return response(calls.length===1?{status:'ok',jobs:[job],next_after:id(3)}:calls.length===2?{status:'ok',jobs:[job,{...job,job_id:id(4)}],next_after:null}:{status:'ok',jobs:[],next_after:null});});
 const v=view();await remotePaperJobs(v,paper,version);
 assert.match(v.innerHTML,/취소됨/);assert.match(v.innerHTML,/처리 완료는 학습 완료가 아닙니다/);assert.match(v.innerHTML,/&lt;script&gt;/);
 await v.querySelector('[data-jobs-more]').onclick();assert.equal((v.innerHTML.match(/revision 2/g)||[]).length,2);
 await v.querySelector('[data-jobs-refresh]').onclick();assert.match(v.innerHTML,/처리 작업이 없습니다/);
 assert.ok(calls.every(c=>c[1]==='GET'));assert.match(calls[1][0],/after=/);listeners.get('pagehide')();
});
test('다른 판본·논문의 응답과 장애를 빈 정상 결과로 표시하지 않는다',async t=>{
 for(const wrong of [{...job,server_version_id:'v2'},{...job,paper_id:id(9)}]){
 t.mock.method(globalThis,'fetch',async()=>response({status:'ok',jobs:[wrong]}));const v=view();await remotePaperJobs(v,paper,version);assert.match(v.innerHTML,/확인하지 못했습니다/);assert.doesNotMatch(v.innerHTML,/revision 2/);listeners.get('pagehide')();t.mock.restoreAll();}
});
test('로그아웃 후 늦은 응답은 표시하지 않는다',async t=>{
 let resolve;t.mock.method(globalThis,'fetch',()=>new Promise(r=>resolve=r));const v=view();const pending=remotePaperJobs(v,paper,version);listeners.get('auth:logout')();const before=v.innerHTML;resolve(response({status:'ok',jobs:[job]}));await pending;assert.equal(v.innerHTML,before);assert.equal(v.textContent,'');
});

test('복구 상태와 같은 키의 검토 링크를 표시하고 외부 링크를 거부한다', async t => {
  const path = `/#/reprocessing/${id(5)}/reparse?key=${id(6)}`;
  const base = {...job, job_kind:'reparse', revision:null, web_path:path};
  t.mock.method(globalThis,'fetch',async()=>response({status:'ok',jobs:[
    {...base,status:'publication_pending'},
    {...base,job_id:id(4),status:'published'},
    {...base,job_id:id(7),status:'published',selected_context_id:id(8)},
  ]}));
  const v=view();
  await remotePaperJobs(v,paper,version);
  for (const label of ['게시 대기','게시됨·선택 대기','새 색인 선택됨','PDF 재파싱']) assert.ok(v.innerHTML.includes(label));
  assert.ok(v.innerHTML.includes(`href="${path}"`));
  assert.doesNotMatch(v.innerHTML,/revision null/);
  listeners.get('pagehide')();
  t.mock.restoreAll();
  t.mock.method(globalThis,'fetch',async()=>response({status:'ok',jobs:[{...base,web_path:'https://example.invalid/'}]}));
  const wrong=view();
  await remotePaperJobs(wrong,paper,version);
  assert.match(wrong.innerHTML,/확인하지 못했습니다/);
  assert.doesNotMatch(wrong.innerHTML,/example.invalid/);
  listeners.get('pagehide')();
});
