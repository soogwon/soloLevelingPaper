import test from 'node:test';
import assert from 'node:assert/strict';
import { createUploadIntent } from '../js/lib/upload-intent.js';
const id = '11111111-1111-4111-8111-111111111111';
const response = {intent_id:id,paper_id:id,version_id:id,status:'created',expires_at:'2099-01-01T00:00:00Z'};
const file = {name:'synthetic.pdf',type:'application/pdf',size:8,arrayBuffer:async()=>new TextEncoder().encode('%PDF-1.7').buffer};
const deferred = () => {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function setup(overrides={}) {
  const calls=[];let keys=0;
  const api={createRemoteUploadIntent:async (...args)=>{calls.push(['create',...args]);return {...response};},
    getRemoteUploadIntent:async (...args)=>{calls.push(['get',...args]);return {...response};},...overrides};
  const controller=createUploadIntent({api,newKey:()=>`request-${++keys}`,digest:async()=> 'a'.repeat(64)});
  return {controller,calls};
}
test('선택과 무동의 호출은 전송하지 않고 명시 동의 뒤 메타데이터만 전송한다',async()=>{
  const {controller:c,calls}=setup();await c.select(file);await c.run();assert.equal(calls.length,0);
  await c.run(true);assert.deepEqual(calls[0],['create',{file_name:file.name,file_size:8,content_type:'application/pdf',sha256:'a'.repeat(64)},'request-1']);
  await c.run(true);assert.deepEqual(calls[1],['get',id]);assert.equal(c.snapshot().status,'created');
});
test('생성 응답 유실 뒤 같은 키와 입력으로 재조회하며 중복 클릭은 보내지 않는다',async()=>{
  const pending=deferred(),calls=[];
  const {controller:c}=setup({createRemoteUploadIntent:async(...args)=>{calls.push(args);if(calls.length===1)return pending.promise;return response;}});
  await c.select(file);const first=c.run(true);await c.run(true);assert.equal(calls.length,1);
  pending.reject(new Error('lost'));await first;assert.equal(c.snapshot().status,'uncertain');
  await c.run(true);assert.deepEqual(calls[0],calls[1]);assert.equal(c.snapshot().status,'created');
});
for(const reset of ['cancel','replace','session']) test(`${reset} 뒤 늦은 생성 응답을 무시한다`,async()=>{
  const pending=deferred();const {controller:c}=setup({createRemoteUploadIntent:()=>pending.promise});
  await c.select(file);const call=c.run(true);
  if(reset==='session')c.close();else await c.select(reset==='cancel'?null:{...file,name:'second.pdf'});
  pending.resolve(response);await call;assert.equal(c.snapshot().intent,null);
  assert.equal(c.snapshot().status,reset==='session'?'closed':reset==='cancel'?'empty':'selected');
});
test('이전 파일의 늦은 해시가 새 선택을 덮지 않는다',async()=>{
  const pending=deferred();const {controller:c}=setup();
  const first=c.select({...file,arrayBuffer:()=>pending.promise});await c.select(null);pending.resolve(await file.arrayBuffer());await first;
  assert.equal(c.snapshot().status,'empty');assert.equal(c.snapshot().canCreate,false);
});
test('상태 응답 유실은 새 의도 대신 동일 ID 조회로 복구한다',async()=>{
  let attempts=0;const {controller:c,calls}=setup({getRemoteUploadIntent:async target=>{assert.equal(target,id);if(++attempts===1)throw new Error('lost');return {...response,status:'verified'};}});
  await c.select(file);await c.run(true);await c.run();assert.equal(c.snapshot().status,'uncertain');
  await c.run();assert.equal(c.snapshot().status,'verified');assert.equal(calls.length,1);
});
test('다른 ID나 알 수 없는 상태 응답을 성공으로 표시하지 않는다',async()=>{
  for(const altered of [{intent_id:'bad'},{status:'ready'},{expires_at:'bad'}]) {
    const {controller:c}=setup({createRemoteUploadIntent:async()=>({...response,...altered})});await c.select(file);await c.run(true);
    assert.equal(c.snapshot().status,'uncertain');assert.equal(c.snapshot().intent,null);
  }
  const {controller:c}=setup({getRemoteUploadIntent:async()=>({...response,paper_id:'22222222-2222-4222-8222-222222222222'})});
  await c.select(file);await c.run(true);await c.run();assert.equal(c.snapshot().status,'uncertain');assert.equal(c.snapshot().intent.paper_id,id);
});
test('만료·권한·충돌 오류는 새 키 재접수를 막는다',async()=>{
  for(const code of [401,403,404,409,410,422]) {
    const {controller:c}=setup({createRemoteUploadIntent:async()=>{throw {status:code};}});
    await c.select(file);await c.run(true);assert.equal(c.snapshot().canCreate,false);
  }
  const {controller:c}=setup({createRemoteUploadIntent:async()=>({...response,expires_at:'2000-01-01T00:00:00Z'})});
  await c.select(file);await c.run(true);assert.equal(c.snapshot().status,'expired');
});
test('잘못된 파일명·크기·형식·실제 바이트 불일치는 전송 전 거부한다',async()=>{
  for(const changes of [{name:'../a.pdf'},{name:'D:note.pdf'},{name:'a.txt'},{size:0},{size:50*1024*1024+1},{size:7},{type:'text/plain'},{arrayBuffer:async()=>new TextEncoder().encode('not pdf!').buffer}]) {
    const {controller:c,calls}=setup();await c.select({...file,...changes});await c.run(true);assert.equal(c.snapshot().status,'invalid');assert.equal(calls.length,0);
  }
});
test('브라우저 MIME이 비어도 PDF 확장자와 바이트 헤더를 검증한다',async()=>{
  const {controller:c}=setup();await c.select({...file,type:''});assert.equal(c.snapshot().status,'selected');
});
test('실제 SHA-256을 파일 바이트로 계산한다',async()=>{
  const {webcrypto}=await import('node:crypto');globalThis.crypto ??= webcrypto;
  let sent;const c=createUploadIntent({api:{createRemoteUploadIntent:async b=>{sent=b;return response;}}});
  await c.select(file);await c.run(true);
  const {createHash}=await import('node:crypto');assert.equal(sent.sha256,createHash('sha256').update('%PDF-1.7').digest('hex'));
});
test('파일 교체 뒤 늦은 오류도 새 선택의 상태를 덮지 않는다',async()=>{
  const pending=deferred();const {controller:c}=setup({createRemoteUploadIntent:()=>pending.promise});
  await c.select(file);const request=c.run(true);await c.select({...file,name:'next.pdf'});
  pending.reject({status:403});await request;
  assert.equal(c.snapshot().status,'selected');assert.equal(c.snapshot().file.name,'next.pdf');
  assert.equal(c.snapshot().canCreate,true);
});
