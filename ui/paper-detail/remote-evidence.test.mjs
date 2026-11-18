import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {mountRemoteEvidence} from './remote-evidence.js';
const ctx='00000000-0000-0000-0000-000000000590', eid='00000000-0000-0000-0000-000000000591';
const aid='00000000-0000-0000-0000-000000000001', bid='00000000-0000-0000-0000-000000000002';
function reply(ids=[aid], next=aid, after=null) {
  return {structuredContent:{status:'ok',result:{context_id:ctx,evidence_id:eid,binding:'a'.repeat(64),after,
    evidence:{context_id:ctx,evidence_id:eid,chunk_id:ctx,parse_revision_id:ctx,version_id:ctx,publication_id:ctx,embedding_set_id:ctx,original_text_sha256:'b'.repeat(64),quote_original:'<img src=x>',quote_ko:'합성 번역',page_notice:'고정 판본',pdf_page:1},
    annotation_page:{items:ids.map(id=>({annotation_id:id,context_id:ctx,kind:'note',body:'합성 메모',revision:1,segments:[{chunk_id:ctx,parse_revision_id:ctx,target:'original',quote_snapshot:'합성 인용'}]})),next_cursor:next}}}};
}
function setup() {
  const doc=new JSDOM('<p id="status"></p><pre id="evidence"></pre><pre id="records"></pre><button id="more"></button><button id="refresh"></button>').window.document;
  const calls=[]; let resolve;
  const app={connect:async()=>{},callServerTool:args=>{calls.push(args);return new Promise(r=>resolve=r);}};
  mountRemoteEvidence(app,doc);
  app.ontoolinput({arguments:{context_id:ctx,evidence_id:eid}});app.ontoolresult(reply());
  return {doc,app,calls,finish:r=>resolve(r)};
}
test('더 보기와 처음부터 새로고침은 같은 근거와 연결을 사용한다',async()=>{
  const s=setup();assert.equal(s.doc.querySelector('img'),null);
  const pending=s.doc.getElementById('more').onclick();
  assert.equal(s.calls[0].arguments.after,aid);assert.equal(s.calls[0].arguments.expected_binding,'a'.repeat(64));
  s.finish(reply([bid],null,aid));await pending;
  assert.equal(s.doc.getElementById('records').textContent.match(/합성 메모/g).length,2);
  const fresh=s.doc.getElementById('refresh').onclick();s.finish(reply([],null));await fresh;
  assert.match(s.doc.getElementById('records').textContent,/없습니다/);
});
for(const event of ['cancel','input','hide'])test(`늦은 응답 차단: ${event}`,async()=>{
  const s=setup(),pending=s.doc.getElementById('more').onclick();
  if(event==='cancel')s.app.ontoolcancelled();
  if(event==='input')s.app.ontoolinput({arguments:{context_id:eid,evidence_id:ctx}});
  if(event==='hide')s.doc.defaultView.dispatchEvent(new s.doc.defaultView.Event('pagehide'));
  s.finish(reply([bid],null,aid));await pending;assert.equal(s.doc.getElementById('records').textContent,'');
});
test('연결 변경과 중복 페이지는 이전 본문까지 지운다',async()=>{
  for(const result of [{structuredContent:{status:'ok',result:{connection_changed:true}}},reply([aid],null,aid)]){
    const s=setup(),pending=s.doc.getElementById('more').onclick();s.finish(result);await pending;
    assert.equal(s.doc.getElementById('records').textContent,'');assert(s.doc.getElementById('more').disabled);
  }
});
