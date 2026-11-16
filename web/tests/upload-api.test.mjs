import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window={location:{search:'',origin:'https://example.test'},dispatchEvent(){}};
globalThis.document={cookie:'csrf_token=test-csrf'};
const {api}=await import('../js/api/index.js');
const {matchRoute}=await import('../js/router.js');
test('실제 API에 쿠키·CSRF·멱등 키를 보내고 조회에는 키를 보내지 않는다',async()=>{
  const calls=[];globalThis.fetch=async(url,options)=>{calls.push({url:String(url),options});return {ok:true,status:200,json:async()=>({})};};
  await api.createRemoteUploadIntent({file_name:'a.pdf'},'same-key');await api.getRemoteUploadIntent('intent/id');
  assert.equal(calls[0].url,'https://example.test/v1/remote-uploads/intents');
  assert.equal(calls[0].options.headers['Idempotency-Key'],'same-key');
  assert.equal(calls[0].options.headers['X-CSRF-Token'],'test-csrf');
  assert.equal(calls[0].options.credentials,'include');
  assert.equal(calls[1].url,'https://example.test/v1/remote-uploads/intents/intent%2Fid');
  assert.equal(calls[1].options.method,'GET');assert.equal(calls[1].options.headers['Idempotency-Key'],undefined);
});
test('업로드 화면은 인증 필수 경로다',()=>{const {route}=matchRoute('/uploads');assert.notEqual(route.public,true);assert.equal(route.parent,'/papers');});
test('동의 조회·승인·철회는 별도 경로와 고지·revision·CSRF를 보낸다',async()=>{
  const calls=[];globalThis.fetch=async(url,options)=>{calls.push({url:String(url),...options});return {ok:true,status:200,json:async()=>({})};};
  await api.getRemoteUploadConsents('intent');
  await api.grantRemoteUploadConsent('intent','storage','v2','storage-key');
  await api.grantRemoteUploadConsent('intent','ai_provider','v2','ai-key');
  await api.revokeRemoteUploadConsent('intent','storage',7);
  assert.deepEqual(calls.map(c=>c.url.split('/v1/')[1]),[
    'remote-uploads/intents/intent/consents','remote-uploads/intents/intent/consents/storage',
    'remote-uploads/intents/intent/consents/ai_provider','remote-uploads/intents/intent/consents/storage/revoke']);
  assert.deepEqual(calls.map(c=>c.method),['GET','POST','POST','POST']);
  assert.equal(calls[1].headers['Idempotency-Key'],'storage-key');assert.equal(calls[2].headers['Idempotency-Key'],'ai-key');
  assert.deepEqual(JSON.parse(calls[1].body),{notice_version:'v2'});
  assert.deepEqual(JSON.parse(calls[3].body),{expected_revision:7});
  for(const call of calls.slice(1))assert.equal(call.headers['X-CSRF-Token'],'test-csrf');
});
test('prepare·complete는 같은 의도에 웹 세션과 CSRF를 보내고 본문·새 키를 만들지 않는다',async()=>{
  const calls=[];globalThis.fetch=async(url,options)=>{calls.push({url:String(url),...options});return {ok:true,status:200,json:async()=>({})};};
  await api.prepareRemoteUpload('intent/id');await api.completeRemoteUpload('intent/id');
  assert.deepEqual(calls.map(x=>x.url),['https://example.test/v1/remote-uploads/intents/intent%2Fid/prepare','https://example.test/v1/remote-uploads/intents/intent%2Fid/complete']);
  for(const call of calls){assert.equal(call.method,'POST');assert.equal(call.credentials,'include');assert.equal(call.headers['X-CSRF-Token'],'test-csrf');assert.equal(call.body,undefined);assert.equal(call.headers['Idempotency-Key'],undefined);}
});
test('처리 상태 조회는 인코딩한 의도에 GET만 보내고 새 작업 키나 본문을 보내지 않는다',async()=>{
  const calls=[];globalThis.fetch=async(url,options)=>{calls.push({url:String(url),...options});return {ok:true,status:200,json:async()=>({})};};
  await api.getRemoteProcessing('intent/id');
  assert.equal(calls[0].url,'https://example.test/v1/remote-uploads/intents/intent%2Fid/processing');
  assert.equal(calls[0].method,'GET');assert.equal(calls[0].credentials,'include');
  assert.equal(calls[0].body,undefined);assert.equal(calls[0].headers['Idempotency-Key'],undefined);
});
