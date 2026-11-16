import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
globalThis.crypto ??= webcrypto;
const listeners = new Map();
globalThis.window = {location:{search:'',origin:'https://example.test'},
  addEventListener:(name,fn)=>listeners.set(name,fn),
  removeEventListener:(name)=>listeners.delete(name),dispatchEvent:()=>{}};
globalThis.document = {cookie:''};
const {api} = await import('../js/api/index.js');
const {default:page} = await import('../js/pages/upload.js');
const id='11111111-1111-4111-8111-111111111111';
const response={intent_id:id,paper_id:id,version_id:id,status:'created',expires_at:'2099-01-01T00:00:00Z'};
function view() {
  const nodes=new Map();
  return {isConnected:true,innerHTML:'',querySelector(selector){
    if(!nodes.has(selector))nodes.set(selector,{value:'',checked:false,disabled:false,textContent:'',events:{},addEventListener(name,fn){this.events[name]=fn;}});
    return nodes.get(selector);
  }};
}
const file={name:'sample.pdf',type:'application/pdf',size:8,arrayBuffer:async()=>new TextEncoder().encode('%PDF-1.7').buffer};
test('제품 화면은 명시 동의 후에만 API로 보내고 취소·로그아웃에서 이전 표시를 지운다',async(t)=>{
  const calls=[];t.mock.method(api,'createRemoteUploadIntent',async(...args)=>{calls.push(args);return response;});
  const v=view();page({view:v});const node=s=>v.querySelector(s);
  assert.match(v.innerHTML,/PDF 업로드 \(준비 중\)/);assert.equal(calls.length,0);
  node('[data-file]').files=[file];await node('[data-file]').events.change();
  assert.equal(node('[data-create]').disabled,true);
  node('[data-agree]').checked=true;node('[data-agree]').events.change();assert.equal(node('[data-create]').disabled,false);
  await node('[data-create]').events.click();assert.equal(calls.length,1);
  assert.match(node('[data-intent]').textContent,new RegExp(id));
  node('[data-file]').files=[];node('[data-file]').events.cancel();assert.equal(node('[data-intent]').textContent,'');
  assert.equal(node('[data-agree]').checked,false);
  node('[data-file]').value='selected';listeners.get('auth:logout')();
  assert.equal(node('[data-file]').value,'');assert.equal(node('[data-create]').disabled,true);assert.equal(listeners.size,0);
});
for (const event of ['auth:logout', 'auth:expired']) test(`${event} 뒤 늦은 응답이 화면에 남지 않는다`,async(t)=>{
  let resolve;const pending=new Promise(r=>resolve=r);
  t.mock.method(api,'createRemoteUploadIntent',()=>pending);
  const v=view();page({view:v});const node=s=>v.querySelector(s);
  node('[data-file]').files=[file];await node('[data-file]').events.change();
  node('[data-agree]').checked=true;const request=node('[data-create]').events.click();
  listeners.get(event)();resolve(response);await request;
  assert.equal(node('[data-intent]').textContent,'');assert.match(node('[data-status]').textContent,/로그인 상태/);
  assert.equal(node('[data-agree]').checked,false);assert.equal(node('[data-create]').disabled,true);
  assert.equal(node('[data-read]').disabled,true);assert.equal(listeners.size,0);
});
test('제품 화면과 실제 API 어댑터가 응답 유실 뒤 같은 키·본문으로 복구한다',async(t)=>{
  const calls=[];let stored=null;
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    calls.push({url:String(url),...options});
    if(options.method==='POST'){
      const request={key:options.headers['Idempotency-Key'],body:options.body};
      if(!stored){stored=request;return {ok:true,status:201,json:async()=>{throw new SyntaxError('응답 본문 유실');}};}
      assert.deepEqual(request,stored);
    }
    return {ok:true,status:200,json:async()=>response};
  });
  const v=view();page({view:v});const node=s=>v.querySelector(s);
  node('[data-file]').files=[file];await node('[data-file]').events.change();
  await node('[data-create]').events.click();assert.equal(calls.length,0);
  node('[data-agree]').checked=true;node('[data-agree]').events.change();
  await node('[data-create]').events.click();
  assert.match(node('[data-status]').textContent,/결과를 확인하지 못/);
  assert.equal(node('[data-create]').textContent,'같은 등록 준비 요청 확인');
  assert.equal(node('[data-create]').disabled,false);assert.equal(node('[data-intent]').textContent,'');
  await node('[data-create]').events.click();
  assert.match(node('[data-intent]').textContent,new RegExp(id));
  await node('[data-read]').events.click();
  assert.deepEqual(calls.map(c=>c.method),['POST','POST','GET']);
  assert.equal(calls[2].url,`https://example.test/v1/remote-uploads/intents/${id}`);
  assert.deepEqual(Object.keys(JSON.parse(stored.body)).sort(),['content_type','file_name','file_size','sha256']);
  listeners.get('auth:logout')();
});
test('로그아웃 뒤 늦은 API 오류도 닫힌 상태를 덮지 않는다',async(t)=>{
  let reject;const pending=new Promise((_resolve,r)=>reject=r);
  t.mock.method(api,'createRemoteUploadIntent',()=>pending);
  const v=view();page({view:v});const node=s=>v.querySelector(s);
  node('[data-file]').files=[file];await node('[data-file]').events.change();
  node('[data-agree]').checked=true;const request=node('[data-create]').events.click();
  listeners.get('auth:logout')();reject({status:403});await request;
  assert.match(node('[data-status]').textContent,/로그인 상태/);
  assert.equal(node('[data-intent]').textContent,'');assert.equal(node('[data-read]').disabled,true);
});
