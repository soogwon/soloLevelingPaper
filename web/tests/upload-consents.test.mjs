import test from 'node:test';
import assert from 'node:assert/strict';
import {createUploadConsents} from '../js/lib/upload-consents.js';
const id='11111111-1111-4111-8111-111111111111';
const record=(purpose,extra={})=>({consent_id:id,purpose,state:'current',revision:1,
  notice_version:'v1',granted_at:'2026-01-01T00:00:00Z',revoked_at:null,...extra});
const notice=(extra={})=>({intent_id:id,notice_version:'v1',storage_notice:'보관 고지',
  ai_provider_notice:'OpenAI 전송 고지',storage:null,ai_provider:null,...extra});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function setup(overrides={}) {
  const calls=[];let keys=0;
  const api={getRemoteUploadConsents:async (...args)=>{calls.push(['read',...args]);return notice();},
    grantRemoteUploadConsent:async (...args)=>{calls.push(['grant',...args]);return record(args[1]);},
    revokeRemoteUploadConsent:async (...args)=>{calls.push(['revoke',...args]);return record(args[1],{state:'revoked',revoked_at:'2026-01-02T00:00:00Z'});},...overrides};
  const c=createUploadConsents({api,newKey:()=>`key-${++keys}`});
  c.bind({intent_id:id,status:'created'});
  return {c,calls};
}
test('조회와 명시 확인 없이 동의하지 않고 보관과 AI 승인을 분리한다',async()=>{
  const {c,calls}=setup();await c.grant('storage',true);assert.equal(calls.length,0);
  await c.refresh();await c.grant('storage');await c.grant('ai_provider',true);
  assert.deepEqual(calls,[['read',id]]);
  await c.grant('storage',true);
  assert.deepEqual(calls[1],['grant',id,'storage','v1','key-1']);
  assert.equal(c.snapshot().values.ai_provider,null);
  c.bind({intent_id:id,status:'verified'});await c.refresh();await c.grant('ai_provider',true);
  assert.deepEqual(calls.at(-1),['grant',id,'ai_provider','v1','key-2']);
});
test('승인 응답 유실 재시도는 같은 목적·고지·키를 유지한다',async()=>{
  const sent=[];const {c}=setup({grantRemoteUploadConsent:async (...args)=>{
    sent.push(args);if(sent.length===1)throw new Error('lost');return record('storage');}});
  await c.refresh();await c.grant('storage',true);assert.equal(c.snapshot().status,'uncertain');
  await c.grant('storage',true);assert.deepEqual(sent[0],sent[1]);assert.equal(c.snapshot().values.storage.state,'current');
});
test('고지 변경 충돌은 재조회·명시 재확인 전 재승인을 막는다',async()=>{
  let version='v1';const sent=[];
  const {c}=setup({getRemoteUploadConsents:async()=>notice({notice_version:version}),
    grantRemoteUploadConsent:async(...args)=>{sent.push(args);if(version==='v1')throw {status:409};return record('storage',{notice_version:version});}});
  await c.refresh();await c.grant('storage',true);await c.grant('storage',true);
  assert.equal(c.snapshot().status,'refresh');assert.equal(sent.length,1);
  version='v2';await c.refresh();await c.grant('storage');assert.equal(sent.length,1);
  await c.grant('storage',true);assert.equal(sent[1][2],'v2');assert.notEqual(sent[0][3],sent[1][3]);
});
test('철회는 최신 revision을 보내고 응답 유실 후 재조회로 복구한다',async()=>{
  let revoked=false;const calls=[];
  const {c}=setup({getRemoteUploadConsents:async()=>notice({storage:record('storage',revoked
    ? {revision:7,state:'revoked',revoked_at:'2026-01-02T00:00:00Z'} : {revision:7})}),
    revokeRemoteUploadConsent:async(...args)=>{calls.push(args);revoked=true;throw new Error('lost');}});
  await c.refresh();await c.revoke('storage');assert.deepEqual(calls,[[id,'storage',7]]);
  await c.revoke('storage');assert.equal(calls.length,1);
  await c.refresh();assert.equal(c.snapshot().values.storage.state,'revoked');
});
for(const action of ['read','grant','revoke']) for(const reset of ['cancel','logout']) {
  test(`${action} 중 ${reset} 뒤 늦은 응답을 무시한다`,async()=>{
    const pending=deferred();
    const {c}=setup({getRemoteUploadConsents:async()=>action==='read'?pending.promise:notice({storage:action==='revoke'?record('storage'):null}),
      grantRemoteUploadConsent:()=>pending.promise,revokeRemoteUploadConsent:()=>pending.promise});
    let request;
    if(action==='read')request=c.refresh();
    else {await c.refresh();request=action==='grant'?c.grant('storage',true):c.revoke('storage');}
    if(reset==='logout')c.close();else c.bind(null);
    pending.resolve(action==='read'?notice():record('storage',{state:'revoked',revoked_at:'2026-01-02T00:00:00Z'}));await request;
    assert.equal(c.snapshot().notice,null);assert.equal(c.snapshot().values.storage,null);
    assert.equal(c.snapshot().canRead,false);
  });
}
test('다른 의도·목적·잘못된 revision 응답을 동의 성공으로 표시하지 않는다',async()=>{
  for(const bad of [{intent_id:'other'},{storage:record('ai_provider')},{storage:record('storage',{revision:0})}]){
    const {c}=setup({getRemoteUploadConsents:async()=>notice(bad)});await c.refresh();
    assert.equal(c.snapshot().status,'uncertain');assert.equal(c.snapshot().notice,null);
  }
});
test('미승인과 미확인을 구분하고 실패·만료 의도에는 승인하지 않는다',async()=>{
  const {c,calls}=setup();for(const status of ['failed','expired']){
    c.bind({intent_id:id,status});await c.refresh();await c.grant('storage',true);
  }assert.equal(calls.length,0);
});
test('철회 revision 충돌 뒤 새 상태 조회 전 반복 변경을 막는다',async()=>{
  const {c}=setup({getRemoteUploadConsents:async()=>notice({storage:record('storage')}),revokeRemoteUploadConsent:async()=>{throw {status:409};}});
  await c.refresh();await c.revoke('storage');assert.equal(c.snapshot().status,'refresh');
  assert.equal(c.snapshot().canRevoke.storage,false);assert.equal(c.snapshot().canGrant.storage,false);
});
test('현재 동의는 재승인하지 않으며 stale 고지는 명시 확인으로 갱신한다',async()=>{
  const {c,calls}=setup({getRemoteUploadConsents:async()=>notice({storage:record('storage'),
    ai_provider:record('ai_provider',{state:'stale',notice_version:'old'})})});
  c.bind({intent_id:id,status:'verified'});await c.refresh();await c.grant('storage',true);
  assert.equal(c.snapshot().canGrant.storage,false);assert.equal(c.snapshot().canGrant.ai_provider,true);
  await c.grant('ai_provider',true);assert.equal(calls.length,1);assert.equal(calls[0][1],id);
  assert.equal(c.snapshot().values.ai_provider.state,'current');
});
test('진행 중 중복 승인은 보내지 않고 로그아웃 뒤 늦은 오류도 무시한다',async()=>{
  const pending=deferred();let count=0;
  const {c}=setup({grantRemoteUploadConsent:()=>{count++;return pending.promise;}});
  await c.refresh();const request=c.grant('storage',true);await c.grant('storage',true);
  assert.equal(count,1);c.close();pending.reject({status:409});await request;
  assert.equal(c.snapshot().status,'empty');assert.equal(c.snapshot().notice,null);
});
