import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';
import {mountRemoteCompose} from './remote-compose.js';
const ctx='00000000-0000-0000-0000-000000000580',id='00000000-0000-0000-0000-000000000502';
const key='00000000-0000-0000-0000-000000000001';
const value={kind:'note',body:'합성 메모'};
const saved={request_key:key,annotation:{annotation_id:id,context_id:ctx,kind:'note',body:value.body,segments:[],revision:1},state:'committed',recovery:'unique_unconfirmed'};
function setup(sender){
 const doc=new JSDOM(readFileSync(new URL('./remote-compose-template.html',import.meta.url),'utf8')).window.document;
 const calls=[];let keys=0;const app={connect:async()=>{},callServerTool:async a=>{calls.push(a);return sender(a);}};
 mountRemoteCompose(app,doc,{randomUUID:()=>{keys++;return key;}});
 const show=(existing=null)=>{app.ontoolinput({arguments:{context_id:ctx,value}});app.ontoolresult({structuredContent:{status:'ok',result:{context_id:ctx,value,binding:'a'.repeat(64),existing}}});};
 return {doc,app,calls,show,keys:()=>keys,el:id=>doc.getElementById(id)};
}
const ok=result=>({structuredContent:{status:'ok',result}});
test('준비와 표시만으로 생성하지 않고 명시 확인 후 한 키로 저장한다',async()=>{
 const t=setup(()=>ok(saved));t.show();await t.el('save').onclick();assert.equal(t.keys(),0);
 t.el('new').checked=true;t.el('new').onchange();await t.el('save').onclick();await t.el('save').onclick();
 assert.equal(t.calls.length,1);assert.equal(t.keys(),1);assert.equal(t.calls[0].name,'save_remote_annotation_card');
 assert.equal(t.el('confirm').disabled,false);
});
test('기존 미확정 결과나 삭제 이력은 새 키를 발급하지 않는다',async()=>{
 for(const state of ['committed','deleted']){const t=setup(()=>{});t.show({...saved,state});
 await t.el('save').onclick();assert.equal(t.keys(),0);assert.equal(t.el('new').disabled,true);
 if(state==='deleted'){assert.equal(t.el('record').textContent,'');assert.equal(t.el('confirm').disabled,true);}}
});
test('생성 응답 유실 후 같은 키 조회만 하고 중복 제출하지 않는다',async()=>{
 const t=setup(a=>{if(a.name==='save_remote_annotation_card')throw Error('비밀');return ok({context_id:ctx,request_key:key,binding:'a'.repeat(64),recovery:saved});});t.show();t.el('new').checked=true;
 await t.el('save').onclick();await t.el('save').onclick();await t.el('lookup').onclick();
 assert.deepEqual(t.calls.map(x=>x.name),['save_remote_annotation_card','show_remote_annotation_recovery']);
 assert.equal(t.calls[0].arguments.request_key,t.calls[1].arguments.request_key);assert.equal(t.el('confirm').disabled,false);
});
test('새 입력 뒤 늦은 저장 결과를 표시하거나 확인하지 않는다',async()=>{
 let done;const t=setup(()=>new Promise(r=>done=r));t.show();t.el('new').checked=true;const pending=t.el('save').onclick();
 t.app.ontoolinput({arguments:{context_id:ctx,value:{kind:'note',body:'다른 입력'}}});done(ok(saved));await pending;
 assert.equal(t.el('record').textContent,'');assert.equal(t.el('confirm').disabled,true);assert.equal(t.el('new').checked,false);
});
test('동의·연결 실패를 저장 성공으로 표시하지 않는다',async()=>{
 const t=setup(()=>({structuredContent:{status:'consent_required'}}));t.show();t.el('new').checked=true;await t.el('save').onclick();
 assert.equal(t.el('record').textContent,'');assert.equal(t.el('confirm').disabled,true);assert.equal(t.el('save').disabled,true);
});
test('수신 확정은 사용자 클릭 때만 호출한다',async()=>{
 const t=setup(()=>ok({confirmed:true}));t.show(saved);assert.equal(t.calls.length,0);await t.el('confirm').onclick();
 assert.equal(t.calls[0].name,'confirm_remote_annotation_card');assert.equal(t.calls[0].arguments.revision,1);assert.match(t.el('status').textContent,/확정했습니다/);
});
test('복사한 원문과 메모, 페이지·섹션을 보여준 뒤에만 한 항목을 저장한다', async () => {
 const input = { context_id: ctx, quote: 'Observers recorded 12 signals.', note: '더 확인할 필요 있음' };
 const segments = [
  { chunk_id: id, start_offset: 0, end_offset: 18, quote_snapshot: 'Observers recorded' },
  { chunk_id: ctx, start_offset: 4, end_offset: 16, quote_snapshot: '\n12 signals.' },
 ];
 const value = { kind: 'highlight', body: input.note, segments, creation_origin: 'user' };
 const t = setup(() => ok({ ...saved, annotation: { ...saved.annotation, ...value } }));
 t.app.ontoolinput({ arguments: input });
 t.app.ontoolresult(ok({ context_id: ctx, value, binding: 'a'.repeat(64), existing: null,
  quote_request: { quote: input.quote, note: input.note, pdf_page: null, candidate_id: null },
  quote_location: { pdf_page: 2, section_title: 'Methods' } }));
 assert.match(t.el('input').textContent, /PDF 2페이지 · Methods\nObservers recorded\n12 signals\.\n메모 · 더 확인할 필요 있음/);
 await t.el('save').onclick();
 assert.equal(t.calls.length, 0);
 t.el('new').checked = true;
 t.el('new').onchange();
 await t.el('save').onclick();
 assert.equal(t.calls.length, 1);
 assert.deepEqual(t.calls[0].arguments.value, value);
});
