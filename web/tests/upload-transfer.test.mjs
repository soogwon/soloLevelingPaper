import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {createUploadIntent} from '../js/lib/upload-intent.js';
import {putRemoteUpload} from '../js/api/upload-transfer.js';
globalThis.crypto ??= webcrypto;
const id='11111111-1111-4111-8111-111111111111';
const intent={intent_id:id,paper_id:id,version_id:id,status:'created',expires_at:'2099-01-01T00:00:00Z'};
const consent={consent_id:id,purpose:'storage',revision:1,state:'current',notice_version:'v1',revoked_at:null};
const notice={intent_id:id,notice_version:'v1',storage:consent};
const prepared={intent_id:id,method:'PUT',upload_url:'https://storage.example.test/private/tmp/test?signature=secret',
  headers:{'Content-Type':'application/pdf','Content-Length':'8'},expires_at:intent.expires_at};
const completed={...intent,status:'verified',file_name:'sample.pdf',file_size:8,content_type:'application/pdf'};
const file={name:'sample.pdf',type:'application/pdf',size:8,arrayBuffer:async()=>new TextEncoder().encode('%PDF-1.7').buffer};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
async function setup(overrides={}, options={}) {
  const calls=[];
  const api={createRemoteUploadIntent:async()=>intent,getRemoteUploadIntent:async()=>intent,
    getRemoteUploadConsents:async()=>{calls.push('consent');return notice;},
    prepareRemoteUpload:async()=>{calls.push('prepare');return prepared;},
    putRemoteUpload:async(_prepared,body)=>{calls.push('put');assert.equal(await body.text(),'%PDF-1.7');},
    completeRemoteUpload:async()=>{calls.push('complete');return completed;},...overrides};
  const c=createUploadIntent({api,...options});await c.select(file);await c.run(true);return {c,calls};
}
test('명시 업로드 전 전송하지 않고 보관 동의 재확인 뒤 순서대로 검증한다',async()=>{
  const {c,calls}=await setup();await c.upload();assert.deepEqual(calls,[]);
  await c.upload(consent);
  assert.deepEqual(calls,['consent','prepare','consent','put','consent','complete']);
  assert.equal(c.snapshot().status,'verified');assert.equal(c.snapshot().canUpload,false);
});
for(const point of [1,2,3])test(`${point}번째 동의 확인에서 철회가 보이면 다음 전송·확정을 차단한다`,async()=>{
  let reads=0;const {c,calls}=await setup({getRemoteUploadConsents:async()=>++reads===point
    ? {...notice,storage:{...consent,state:'revoked'}}:notice});
  await c.upload(consent);assert.equal(c.snapshot().status,'consentRequired');
  assert.deepEqual(calls,point===1?[]:point===2?['prepare']:['prepare','put']);
});
test('PUT 응답 유실 후 PDF를 재전송하지 않고 같은 의도 완료로 복구한다',async()=>{
  let puts=0;const {c,calls}=await setup({putRemoteUpload:async()=>{puts++;throw new Error('lost');}});
  await c.upload(consent);assert.equal(c.snapshot().status,'uploadUncertain');
  await c.upload(consent);assert.equal(puts,1);assert.equal(c.snapshot().status,'verified');
  assert.equal(calls.filter(x=>x==='prepare').length,1);
});
test('완료 응답 유실도 완료 재확인만 수행하고 PUT을 반복하지 않는다',async()=>{
  let completes=0;const {c,calls}=await setup({completeRemoteUpload:async()=>{if(++completes===1)throw new Error('lost');return completed;}});
  await c.upload(consent);await c.upload(consent);
  assert.equal(c.snapshot().status,'verified');assert.equal(calls.filter(x=>x==='put').length,1);
});
for(const phase of ['consent','prepare','put','complete'])test(`${phase} 중 취소·재선택·로그아웃 후 다음 단계와 늦은 상태를 차단한다`,async()=>{
  for(const reset of ['cancel','replace','logout']) {
    const pending=deferred();let started;const entered=new Promise(r=>started=r);
    const method={consent:'getRemoteUploadConsents',prepare:'prepareRemoteUpload',put:'putRemoteUpload',complete:'completeRemoteUpload'}[phase];
    let signal;const {c,calls}=await setup({[method]:async(...args)=>{signal=args[2];started();return pending.promise;}});
    const request=c.upload(consent);await entered;
    if(reset==='logout')c.close();else await c.select(reset==='replace'?{...file,name:'new.pdf'}:null);
    const before=c.snapshot();if(phase==='put')assert.equal(signal.aborted,true);
    pending.resolve({consent:notice,prepare:prepared,put:null,complete:completed}[phase]);await request;
    assert.deepEqual(c.snapshot(),before);
    if(phase!=='complete')assert.equal(calls.includes('complete'),false);
  }
});
test('다른 대상·만료 prepare와 잘못된 완료 결과를 성공으로 표시하지 않는다',async()=>{
  for(const bad of [{...prepared,intent_id:'other'},{...prepared,expires_at:'2000-01-01T00:00:00Z'}]) {
    const {c,calls}=await setup({prepareRemoteUpload:async()=>bad});await c.upload(consent);assert.equal(calls.includes('put'),false);
  }
  const {c}=await setup({completeRemoteUpload:async()=>({...completed,version_id:'other'})});
  await c.upload(consent);assert.notEqual(c.snapshot().status,'verified');
});
test('선택 시 해시한 바이트만 보내고 전송 중 중복 클릭은 무시한다',async()=>{
  const pending=deferred();let started;const entered=new Promise(r=>started=r);let count=0;
  const {c}=await setup({putRemoteUpload:async(_p,body)=>{count++;assert.equal(await body.text(),'%PDF-1.7');started();await pending.promise;}});
  const request=c.upload(consent);await entered;await c.upload(consent);assert.equal(count,1);pending.resolve();await request;
});
test('저장소 PUT에는 세션 자격·CSRF·참조 URL 없이 Blob을 보내고 리다이렉트를 거부한다',async(t)=>{
  const calls=[];t.mock.method(globalThis,'fetch',async(...args)=>{calls.push(args);return {ok:true};});
  const body=new Blob(['%PDF-1.7'],{type:'application/pdf'});const signal=new AbortController().signal;
  await putRemoteUpload(prepared,body,signal);
  const options=calls[0][1];assert.equal(options.body,body);assert.equal(options.signal,signal);
  assert.deepEqual(options.headers,{'content-type':'application/pdf'});assert.equal(options.credentials,'omit');
  assert.equal(options.redirect,'error');assert.equal(options.referrerPolicy,'no-referrer');
  for(const bad of [{...prepared,upload_url:'http://storage.example.test'},
    {...prepared,headers:{...prepared.headers,Authorization:'secret'}},
    {...prepared,headers:{...prepared.headers,'Content-Length':'9'}}]) {
    await assert.rejects(putRemoteUpload(bad,body,signal));
  }
  assert.equal(calls.length,1);
});
test('저장소 오류는 서명 URL이나 응답 본문을 노출하지 않는다',async(t)=>{
  t.mock.method(globalThis,'fetch',async()=>{throw new Error(prepared.upload_url);});
  await assert.rejects(putRemoteUpload(prepared,new Blob(['%PDF-1.7'],{type:'application/pdf'})),e=>e.message==='upload_result_unknown');
});

test('서명 주소가 만료됐거나 동의 재조회 중 만료되면 PUT 없이 만료로 표시한다',async()=>{
  for(const duringRead of [false,true]) {
    let clock=0,reads=0;
    const {c,calls}=await setup({
      prepareRemoteUpload:async()=>({...prepared,expires_at:new Date(duringRead?1000:0).toISOString()}),
      getRemoteUploadConsents:async()=>{if(duringRead && ++reads===2)clock=1001;return notice;},
    },{now:()=>clock});
    await c.upload(consent);assert.equal(c.snapshot().status,'expired');
    assert.equal(c.snapshot().canUpload,false);assert.equal(calls.includes('put'),false);
  }
});
test('선택 이후 원본 버퍼가 바뀌어도 해시한 PDF 바이트를 전송한다',async()=>{
  const {c}=await setup();const bytes=new TextEncoder().encode('%PDF-1.7');
  await c.select({...file,arrayBuffer:async()=>bytes.buffer});await c.run(true);
  bytes.fill(0);await c.upload(consent);assert.equal(c.snapshot().status,'verified');
});
test('prepare 응답 유실은 동일 의도 prepare를 재확인하고 PUT은 한 번만 실행한다',async()=>{
  let prepares=0;const targets=[];const {c,calls}=await setup({prepareRemoteUpload:async target=>{
    targets.push(target);if(++prepares===1)throw new Error('lost');return prepared;
  }});
  await c.upload(consent);assert.equal(c.snapshot().putAttempted,false);
  await c.upload(consent);assert.deepEqual(targets,[id,id]);
  assert.equal(calls.filter(x=>x==='put').length,1);assert.equal(c.snapshot().status,'verified');
});
for(const phase of ['prepare','complete'])test(`${phase}에서 서버가 보관 동의를 거부하면 재동의를 안내한다`,async()=>{
  const method=phase==='prepare'?'prepareRemoteUpload':'completeRemoteUpload';
  const {c}=await setup({[method]:async()=>{throw {status:409,code:'remote_upload_storage_consent_required'};}});
  await c.upload(consent);assert.equal(c.snapshot().status,'consentRequired');
});
