import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
import {mountRemoteAnnotation} from './remote-annotation-card.js';
const ctx='00000000-0000-0000-0000-000000000580', id='00000000-0000-0000-0000-000000000502';
function response() {return {structuredContent:{status:'ok',result:{context_id:ctx,request_key:'existing',binding:'a'.repeat(64),recovery:{request_key:'existing',recovery:'exact',state:'committed',annotation:{context_id:ctx,annotation_id:id,kind:'note',body:'<script>합성</script>',segments:[],revision:2}}}}};}
function setup(send) {
 const doc=new JSDOM(readFileSync(new URL('./remote-annotation-template.html',import.meta.url),'utf8')).window.document;
 const calls=[]; const app={connect:async()=>{},callServerTool:async args=>{calls.push(args);return send(args);}};
 mountRemoteAnnotation(app,doc);
 const show=(r=response())=>{app.ontoolinput({arguments:{context_id:ctx,request_key:'existing'}});app.ontoolresult(r);};
 return {doc,app,calls,show,button:id=>doc.getElementById(id)};
}
test('명시 확인 전에는 호출하지 않고 현재 ID와 revision을 확정한다',async()=>{
 const t=setup(()=>({structuredContent:{status:'ok',result:{confirmed:true}}}));t.show();
 assert.equal(t.calls.length,0);assert.equal(t.doc.querySelector('script'),null);
 await t.button('confirm').onclick();assert.equal(t.calls.length,1);
 assert.equal(t.calls[0].name,'confirm_remote_annotation_card');
 assert.equal(t.calls[0].arguments.revision,2);assert.equal(t.calls[0].arguments.annotation_id,id);
 assert.match(t.button('status').textContent,/수신을 확정/);assert.equal(t.button('confirm').disabled,true);
});
test('확정 응답 유실 후 같은 키로 조회하고 다시 확인하며 생성하지 않는다',async()=>{
 let n=0;const t=setup(args=>{if(n++===0)throw Error('비밀');return args.name==='show_remote_annotation_recovery'?response():{structuredContent:{status:'ok',result:{confirmed:true}}};});t.show();
 await t.button('confirm').onclick();assert.doesNotMatch(t.button('status').textContent,/비밀/);
 await t.button('refresh').onclick();await t.button('confirm').onclick();
 assert.deepEqual(t.calls.map(c=>c.name),['confirm_remote_annotation_card','show_remote_annotation_recovery','confirm_remote_annotation_card']);
 assert.ok(t.calls.every(c=>c.arguments.request_key==='existing'));
});
for(const change of ['cancel','input','pagehide']) test(`${change} 뒤 늦은 확인 응답은 버린다`,async()=>{
 let done;const t=setup(()=>new Promise(r=>done=r));t.show();const pending=t.button('confirm').onclick();
 if(change==='cancel')t.app.ontoolcancelled();
 if(change==='input')t.app.ontoolinput({arguments:{context_id:ctx,request_key:'new'}});
 if(change==='pagehide')t.doc.defaultView.dispatchEvent(new t.doc.defaultView.Event('pagehide'));
 done({structuredContent:{status:'ok',result:{confirmed:true}}});await pending;
 assert.doesNotMatch(t.button('status').textContent,/수신을 확정했습니다/);assert.equal(t.button('record').textContent,'');
});
test('삭제된 결과에 본문이 섞여도 표시·확정하지 않는다',()=>{
 const t=setup(()=>{});const r=response();r.structuredContent.result.recovery.state='deleted';t.show(r);
 assert.equal(t.button('record').textContent,'');assert.equal(t.button('confirm').disabled,true);assert.equal(t.calls.length,0);
});
test('다른 맥락·키·무효 revision 응답을 거부한다',()=>{
 for(const mutate of [r=>r.structuredContent.result.context_id=id,r=>r.structuredContent.result.request_key='wrong',r=>r.structuredContent.result.recovery.annotation.revision=0]){
 const t=setup(()=>{});const r=response();mutate(r);t.show(r);assert.equal(t.button('confirm').disabled,true);assert.equal(t.button('record').textContent,'');}
});
test('재조회 연결이 바뀌면 이전 본문을 지우고 확인하지 않는다',async()=>{
 const t=setup(()=>{const r=response();r.structuredContent.result.binding='b'.repeat(64);return r;});t.show();
 await t.button('refresh').onclick();assert.equal(t.button('confirm').disabled,true);assert.equal(t.button('record').textContent,'');
});
test('취소 뒤 호스트 결과가 도착해도 카드를 다시 활성화하지 않는다',()=>{
 const t=setup(()=>{});t.app.ontoolinput({arguments:{context_id:ctx,request_key:'existing'}});t.app.ontoolcancelled();t.app.ontoolresult(response());assert.equal(t.button('confirm').disabled,true);
});
