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
