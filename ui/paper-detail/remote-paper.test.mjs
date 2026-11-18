import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {mountRemotePaper} from './remote-paper.js';
const ctx='00000000-0000-0000-0000-000000000590', aid='00000000-0000-0000-0000-000000000591';
function reply(offset=0,after=null) {return {structuredContent:{status:'ok',result:{context_id:ctx,binding:'a'.repeat(64),section_offset:offset,after,
  detail:{context_id:ctx,paper_id:ctx,version_id:'v1',publication_id:ctx,parse_revision_id:ctx,embedding_set_id:ctx,title:'<img src=x>',
    section_catalog:[{section_id:'s',parse_revision_id:ctx,title:'합성 목차',start_pdf_page:1,end_pdf_page:1}],next_section_offset:offset?null:20,
    summary:null,keywords:[],important_sentences:[],generation_notice:'생성하지 않습니다.',page_notice:'고정 판본'},
  annotation_page:{items:[{annotation_id:aid,context_id:ctx,kind:'note',revision:1,body:'연결 없는 메모',segments:[]}],next_cursor:after?null:aid}}}};}
function setup(){
  const doc=new JSDOM('<p id="status"></p><pre id="paper"></pre><pre id="sections"></pre><pre id="records"></pre><button id="sections-more"></button><button id="records-more"></button><button id="refresh"></button>').window.document;
  let resolve;const calls=[];
  const app={connect:async()=>{},callServerTool:a=>{calls.push(a);return new Promise(r=>resolve=r);}};
  mountRemotePaper(app,doc);app.ontoolinput({arguments:{context_id:ctx}});app.ontoolresult(reply());
  return {doc,app,calls,finish:r=>resolve(r)};
}
test('목차와 전체 기록 페이지를 독립 조회하고 새로고침한다',async()=>{
  const s=setup();assert.equal(s.doc.querySelector('img'),null);assert.match(s.doc.getElementById('records').textContent,/연결 없는 메모/);
  const next=s.doc.getElementById('sections-more').onclick();s.finish(reply(20));await next;
  assert.equal(s.calls[0].arguments.section_offset,20);
  const records=s.doc.getElementById('records-more').onclick();s.finish(reply(20,aid));await records;
  assert.equal(s.calls[1].arguments.after,aid);
  const fresh=s.doc.getElementById('refresh').onclick();s.finish(reply());await fresh;
  assert.equal(s.calls[2].arguments.section_offset,0);assert.equal(s.calls[2].arguments.after,null);
});
for(const event of ['input','cancel','pagehide'])test(`늦은 상세 응답 차단 ${event}`,async()=>{
  const s=setup(),pending=s.doc.getElementById('sections-more').onclick();
  if(event==='input')s.app.ontoolinput({arguments:{context_id:aid}});
  if(event==='cancel')s.app.ontoolcancelled();
  if(event==='pagehide')s.doc.defaultView.dispatchEvent(new s.doc.defaultView.Event('pagehide'));
  s.finish(reply(20));await pending;assert.equal(s.doc.getElementById('records').textContent,'');
});
test('다른 연결·게시본·맥락·잘못된 커서는 이전 결과까지 지운다',async()=>{
  for(const mutate of [r=>r.binding='b'.repeat(64),r=>r.detail.publication_id=aid,r=>r.annotation_page.items[0].context_id=aid,r=>r.annotation_page.next_cursor=ctx]){
    const s=setup(),pending=s.doc.getElementById('sections-more').onclick(),r=reply(20);mutate(r.structuredContent.result);s.finish(r);await pending;
    assert.equal(s.doc.getElementById('records').textContent,'');assert(s.doc.getElementById('refresh').disabled);
  }
});
