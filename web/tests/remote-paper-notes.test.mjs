import test from 'node:test';
import assert from 'node:assert/strict';
const listeners = new Map();
globalThis.window = { location: { origin: 'https://example.test', search: '' },
  addEventListener: (k, f) => listeners.set(k, f), removeEventListener: k => listeners.delete(k), dispatchEvent() {} };
globalThis.document = { cookie: '' };
const { remotePaperNotes } = await import('../js/lib/remote-paper-notes.js');
const id = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
const paper = id(1), version = id(2), context = id(3);
const contexts = { items: [{ context_id: context, paper_id: paper, server_version_id: version }], next_cursor: null };
const notes = { items: [{ context_id: context, annotation_id: id(4), revision: 1, kind: 'highlight', body: '<script>본문</script>', segments: [{ target: 'translation', quote_snapshot: '번역 근거' }] }], next_cursor: null };
const response = data => ({ok:true,status:200,json:async()=>data});
function view() { const nodes = new Map(); return { isConnected:true, innerHTML:'',textContent:'',
  querySelector(s) { if (!nodes.has(s)) nodes.set(s, {}); return nodes.get(s); }, querySelectorAll(){return [];} }; }

test('현재 판본 GET만 사용해 기록과 관리 링크를 표시한다', async t => {
  const calls=[];
  t.mock.method(globalThis,'fetch',async(url,opts)=>{calls.push([String(url),opts.method]);return response(calls.length===1?contexts:notes);});
  const v=view(); await remotePaperNotes(v,paper,version);
  assert.equal(calls.length,2); assert.ok(calls.every(c=>c[1]==='GET'));
  assert.match(calls[0][0],new RegExp(`version_id=${version}`));
  assert.match(v.innerHTML,/&lt;script&gt;본문/); assert.match(v.innerHTML,/번역 근거/);
  assert.ok(v.innerHTML.includes(`#/remote-learning/${context}`)); listeners.get('pagehide')();
});

test('로그아웃 후 늦은 응답은 후속 조회와 표시를 차단한다', async t => {
  let resolve; let count=0;
  t.mock.method(globalThis,'fetch',()=>{count++;return new Promise(r=>{resolve=r;});});
  const v=view();const pending=remotePaperNotes(v,paper,version);
  listeners.get('auth:logout')();const before=v.innerHTML;
  resolve(response(contexts));await pending;
  assert.equal(count,1);assert.equal(v.innerHTML,before);assert.equal(v.textContent,'');
});

test('다른 판본 맥락이나 다른 맥락 메모 응답을 표시하지 않는다', async t => {
  for (const wrongContext of [true,false]) {
    let calls=0;
    t.mock.method(globalThis,'fetch',async()=>response(++calls===1
      ? wrongContext?{...contexts,items:[{...contexts.items[0],server_version_id:id(99)}]}:contexts
      : {...notes,items:[{...notes.items[0],context_id:id(99)}]}));
    const v=view();await remotePaperNotes(v,paper,version);
    assert.doesNotMatch(v.innerHTML,/번역 근거/);assert.match(v.innerHTML,/조회하지 못했습니다/);
    listeners.get('pagehide')(); t.mock.restoreAll();
  }
});

test('기록 더 보기는 중복 ID를 합치고 새로고침은 현재 목록으로 교체한다', async t => {
  const calls=[]; let rounds=0;
  t.mock.method(globalThis,'fetch',async(url)=>{
    calls.push(String(url));
    if (!String(url).includes('/annotations')) return response(contexts);
    rounds++;return response(rounds===1?{...notes,next_cursor:id(4)}:rounds===2
      ? {items:[...notes.items,{...notes.items[0],annotation_id:id(5),body:'두 번째'}],next_cursor:null}
      : {items:[],next_cursor:null});
  });
  const v=view();await remotePaperNotes(v,paper,version);
  await v.querySelector('[data-more]').onclick();
  assert.equal((v.innerHTML.match(/&lt;script&gt;본문/g)||[]).length,1);
  assert.match(v.innerHTML,/두 번째/); assert.ok(calls[2].includes(`after=${id(4)}`));
  await v.querySelector('[data-refresh]').onclick();assert.doesNotMatch(v.innerHTML,/두 번째/);
  listeners.get('pagehide')();
});

test('메모 응답 도중 세션 만료 시 이전 계정 내용을 표시하지 않는다', async t => {
  let resolve, reached;const pendingFetch=new Promise(r=>{reached=r;});
  t.mock.method(globalThis,'fetch',async url=>{
    if (!String(url).includes('/annotations')) return response(contexts);
    reached();return new Promise(r=>{resolve=r;});
  });
  const v=view();const work=remotePaperNotes(v,paper,version);await pendingFetch;
  listeners.get('auth:expired')();const before=v.innerHTML;
  resolve(response(notes));await work;
  assert.equal(v.innerHTML,before);assert.equal(v.textContent,'');
});

test('문자열 판본을 보존하고 재조회 실패를 빈 목록으로 숨기지 않는다', async t => {
  let count=0;
  t.mock.method(globalThis,'fetch',async()=>{
    count++;if(count>2)throw Error('연결 실패');
    return response(count===1?{...contexts,items:[{...contexts.items[0],server_version_id:'v1'}]}:notes);
  });
  const v=view();await remotePaperNotes(v,paper,'v1');assert.match(v.innerHTML,/번역 근거/);
  await v.querySelector('[data-refresh]').onclick();
  assert.doesNotMatch(v.innerHTML,/번역 근거/);assert.match(v.innerHTML,/조회하지 못했습니다/);
  listeners.get('pagehide')();
});
