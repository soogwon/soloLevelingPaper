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

test('실제 조회 어댑터와 화면이 제한 안내를 표시하고 다음 조회에서 이전 사유를 지운다',async(t)=>{
  t.mock.method(api,'createRemoteUploadIntent',async()=>response);
  let value={job_id:id,paper_id:id,version_id:id,status:'failed',error_code:'usage_pending',retryable:true,detail:'<script>SECRET</script>'};
  const calls=[];
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    calls.push({url:String(url),method:options.method});
    assert.equal(options.method,'GET');
    assert.ok(String(url).endsWith(`/remote-uploads/intents/${id}/processing`));
    return {ok:true,status:200,json:async()=>value};
  });
  const v=view();page({view:v});const node=s=>v.querySelector(s);
  node('[data-file]').files=[file];await node('[data-file]').events.change();
  node('[data-agree]').checked=true;await node('[data-create]').events.click();
  assert.equal(calls.length,0);
  await node('[data-processing-read]').events.click();
  assert.match(node('[data-processing-status]').textContent,/사용량과 비용을 확인 중/);
  assert.match(node('[data-processing-status]').textContent,/미과금이나 환급이 확정된 상태가 아니며/);
  assert.doesNotMatch(node('[data-processing-status]').textContent,/SECRET|<script>/);
  assert.equal(node('[data-processing-read]').disabled,false);
  value={...value,error_code:'budget_unavailable'};
  await node('[data-processing-read]').events.click();
  assert.match(node('[data-processing-status]').textContent,/예산 또는 사용량 한도/);
  assert.doesNotMatch(node('[data-processing-status]').textContent,/사용량과 비용을 확인 중/);
  value={job_id:id,paper_id:id,version_id:id,status:'failed'};
  await node('[data-processing-read]').events.click();
  assert.match(node('[data-processing-status]').textContent,/상세 사유는 아직 제공되지/);
  assert.equal(calls.length,3);
  listeners.get('auth:logout')();
  assert.match(node('[data-processing-status]').textContent,/다시 열어/);
  assert.equal(node('[data-processing-job]').textContent,'');
  assert.equal(node('[data-processing-read]').disabled,true);
});
test('제품 화면은 두 동의를 구분하고 조회·고지 확인 뒤에만 각각 승인한다',async(t)=>{
  const calls=[];let status='created';
  const record=p=>({consent_id:id,purpose:p,revision:1,state:'current',notice_version:'v1',granted_at:'2026-01-01T00:00:00Z',revoked_at:null});
  t.mock.method(api,'createRemoteUploadIntent',async()=>response);
  t.mock.method(api,'getRemoteUploadIntent',async()=>({...response,status}));
  t.mock.method(api,'getRemoteUploadConsents',async()=>({intent_id:id,notice_version:'v1',storage_notice:'<b>합성 보관 고지</b>',ai_provider_notice:'합성 AI 고지',storage:null,ai_provider:null}));
  t.mock.method(api,'grantRemoteUploadConsent',async(...args)=>{calls.push(args);return record(args[1]);});
  const v=view();page({view:v});const node=s=>v.querySelector(s);
  assert.match(v.innerHTML,/PDF 보관 동의/);assert.match(v.innerHTML,/AI 전송 동의/);
  node('[data-file]').files=[file];await node('[data-file]').events.change();
  node('[data-agree]').checked=true;await node('[data-create]').events.click();
  assert.equal(node('[data-grant-storage]').disabled,true);
  await node('[data-consent-read]').events.click();
  assert.equal(node('[data-notice-storage]').textContent,'<b>합성 보관 고지</b>');
  assert.equal(node('[data-approve-ai_provider]').disabled,true);
  await node('[data-grant-storage]').events.click();assert.equal(calls.length,0);
  node('[data-approve-storage]').checked=true;node('[data-approve-storage]').events.change();
  await node('[data-grant-storage]').events.click();assert.equal(calls[0][1],'storage');
  assert.equal(node('[data-approve-storage]').checked,false);
  status='verified';await node('[data-read]').events.click();await node('[data-consent-read]').events.click();
  node('[data-approve-ai_provider]').checked=true;node('[data-approve-ai_provider]').events.change();
  await node('[data-grant-ai_provider]').events.click();assert.equal(calls[1][1],'ai_provider');
  listeners.get('auth:logout')();assert.equal(node('[data-notice-storage]').textContent,'');
  assert.equal(node('[data-grant-ai_provider]').disabled,true);
});
test('제품 화면은 명시 동의 후에만 API로 보내고 취소·로그아웃에서 이전 표시를 지운다',async(t)=>{
  const calls=[];t.mock.method(api,'createRemoteUploadIntent',async(...args)=>{calls.push(args);return response;});
  const v=view();page({view:v});const node=s=>v.querySelector(s);
  assert.match(v.innerHTML,/data-upload disabled>PDF 업로드/);assert.equal(calls.length,0);
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
test('제품 버튼과 실제 어댑터가 대역 prepare·PUT·complete를 연결하고 AI 처리는 시작하지 않는다',async(t)=>{
  const calls=[];let stored=false,verified=false;
  const storage={consent_id:id,purpose:'storage',revision:1,state:'current',notice_version:'v1',granted_at:'2026-01-01T00:00:00Z',revoked_at:null};
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    const path=String(url);calls.push({path,...options});let data;
    if(path==='https://storage.example.test/test?signature=synthetic'){
      assert.equal(options.method,'PUT');assert.equal(await options.body.text(),'%PDF-1.7');
      assert.equal(options.credentials,'omit');return {ok:true,status:200};
    }
    if(path.endsWith('/consents/storage')){stored=true;data=storage;}
    else if(path.endsWith('/consents'))data={intent_id:id,notice_version:'v1',storage_notice:'보관',ai_provider_notice:'AI',storage:stored?storage:null,ai_provider:null};
    else if(path.endsWith('/prepare'))data={intent_id:id,method:'PUT',upload_url:'https://storage.example.test/test?signature=synthetic',headers:{'Content-Type':'application/pdf','Content-Length':'8'},expires_at:response.expires_at};
    else if(path.endsWith('/complete')){verified=true;data={...response,status:'verified',file_name:'sample.pdf',file_size:8,content_type:'application/pdf'};}
    else data={...response,status:verified?'verified':'created'};
    return {ok:true,status:200,json:async()=>data};
  });
  const v=view();page({view:v});const node=s=>v.querySelector(s);
  node('[data-file]').files=[file];await node('[data-file]').events.change();
  node('[data-agree]').checked=true;await node('[data-create]').events.click();
  await node('[data-consent-read]').events.click();
  assert.equal(node('[data-upload]').disabled,true);await node('[data-upload]').events.click();
  assert.equal(calls.some(x=>x.path.endsWith('/prepare')),false);
  node('[data-approve-storage]').checked=true;await node('[data-grant-storage]').events.click();
  assert.equal(node('[data-upload]').disabled,false);assert.equal(node('[data-approve-ai_provider]').disabled,true);
  await node('[data-upload]').events.click();
  assert.match(node('[data-status]').textContent,/PDF 검증이 완료/);
  assert.equal(node('[data-upload]').disabled,true);
  await node('[data-consent-read]').events.click();
  assert.equal(node('[data-approve-ai_provider]').disabled,false);
  assert.deepEqual(calls.filter(x=>x.path.endsWith('/prepare')||x.method==='PUT'||x.path.endsWith('/complete')).map(x=>x.method),['POST','PUT','POST']);
  assert.equal(calls.some(x=>x.path.includes('/processing')||x.path.endsWith('/consents/ai_provider')),false);
  listeners.get('auth:logout')();
});
test('처리 조회 버튼은 기존 작업을 표시하고 파일 취소 시 지운다',async(t)=>{
  t.mock.method(api,'createRemoteUploadIntent',async()=>response);
  const calls=[];
  t.mock.method(api,'getRemoteProcessing',async key=>{calls.push(key);return {job_id:id,paper_id:id,version_id:id,status:'running'};});
  const v=view();page({view:v});const node=s=>v.querySelector(s);
  assert.equal(node('[data-processing-read]').disabled,true);
  node('[data-file]').files=[file];await node('[data-file]').events.change();
  node('[data-agree]').checked=true;await node('[data-create]').events.click();
  assert.equal(calls.length,0);assert.equal(node('[data-processing-read]').disabled,false);
  await node('[data-processing-read]').events.click();assert.deepEqual(calls,[id]);
  assert.match(node('[data-processing-status]').textContent,/처리 중/);
  assert.match(node('[data-processing-job]').textContent,new RegExp(id));
  node('[data-file]').files=[];await node('[data-file]').events.cancel();
  assert.equal(node('[data-processing-job]').textContent,'');assert.equal(node('[data-processing-read]').disabled,true);
  listeners.get('auth:logout')();
});
for(const event of ['auth:logout','auth:expired','hashchange','pagehide'])test(`처리 조회 중 ${event} 뒤 작업 결과를 표시하지 않는다`,async(t)=>{
  t.mock.method(api,'createRemoteUploadIntent',async()=>response);
  let resolve;const pending=new Promise(r=>resolve=r);
  t.mock.method(api,'getRemoteProcessing',()=>pending);
  const v=view();page({view:v});const node=s=>v.querySelector(s);
  node('[data-file]').files=[file];await node('[data-file]').events.change();
  node('[data-agree]').checked=true;await node('[data-create]').events.click();
  const request=node('[data-processing-read]').events.click();listeners.get(event)();
  resolve({job_id:id,paper_id:id,version_id:id,status:'succeeded'});await request;
  assert.equal(node('[data-processing-job]').textContent,'');
  assert.match(node('[data-processing-status]').textContent,/다시 열어/);
  assert.equal(node('[data-processing-read]').disabled,true);
});
test('가입 때 동의한 계정은 동의 단계 없이 버튼 하나로 업로드하고 처리 접수까지 한다',async(t)=>{
  const {accountConsents}=await import('../js/lib/account-consents.js');
  const item=(purpose,state)=>({purpose,required:purpose==='storage',notice:'합성',state,revision:1});
  accountConsents.value={notice_version:'account-consent-v1',complete:true,items:[item('storage','current'),item('ai_provider','current')]};
  t.after(()=>{accountConsents.value=null;});
  const order=[];const record={consent_id:id,purpose:'storage',revision:1,state:'current',notice_version:'v1',granted_at:'2026-01-01T00:00:00Z',revoked_at:null};
  t.mock.method(api,'createRemoteUploadIntent',async()=>{order.push('create');return response;});
  // 서버는 검증이 끝난 뒤에만 계정 동의로 AI 전송 동의를 기록한다.
  let verified=false;const aiRecord={...record,consent_id:'00000000-0000-0000-0000-0000000000c2',purpose:'ai_provider'};
  t.mock.method(api,'getRemoteUploadConsents',async()=>{order.push('consents');return {intent_id:id,notice_version:'v1',storage_notice:'보관',ai_provider_notice:'AI',storage:record,ai_provider:verified?aiRecord:null};});
  const admitted=[];
  t.mock.method(api,'admitRemoteProcessing',async(...args)=>{order.push('admit');admitted.push(args);return {job_id:id,paper_id:id,version_id:id,status:'queued'};});
  t.mock.method(api,'grantRemoteUploadConsent',async()=>{throw new Error('동의를 직접 기록하면 안 됨');});
  t.mock.method(api,'prepareRemoteUpload',async()=>{order.push('prepare');return {intent_id:id,method:'PUT',url:'https://objects.test/put',headers:{},expires_at:'2099-01-01T00:00:00Z'};});
  t.mock.method(api,'putRemoteUpload',async()=>{order.push('put');});
  t.mock.method(api,'completeRemoteUpload',async()=>{order.push('complete');verified=true;return {...response,status:'verified',file_name:'sample.pdf',file_size:8,content_type:'application/pdf'};});
  t.mock.method(api,'getRemoteProcessing',async()=>{order.push('processing');return {job_id:id,paper_id:id,version_id:id,status:'queued'};});
  const v=view();page({view:v});const node=s=>v.querySelector(s);
  assert.match(v.innerHTML,/새 PDF 업로드/);assert.match(v.innerHTML,/data-quick disabled>업로드/);
  assert.match(v.innerHTML,/<label hidden><input type="checkbox" data-agree>/);
  node('[data-file]').files=[file];await node('[data-file]').events.change();
  assert.equal(node('[data-quick]').disabled,false);
  await node('[data-quick]').events.click();
  const done=order.indexOf('complete');
  assert.deepEqual(order.slice(0,done+1).filter((s,i,a)=>a.indexOf(s)===i),['create','consents','prepare','put','complete']);
  assert.deepEqual(order.slice(done+1),['consents','admit','processing']);
  assert.equal(admitted.length,1);
  assert.deepEqual(admitted[0].slice(0,2),[id,{storage_consent_id:id,ai_consent_id:aiRecord.consent_id}]);
  assert.match(node('[data-status]').textContent,/업로드를 마쳤어요. 학습 자료 만들기를 접수했어요/);
  listeners.get('auth:logout')();
});
test('화면 이탈 안내는 업로드 확인 전에는 머물라고, 완료 뒤에는 이동해도 된다고 알린다',async()=>{
  const {leaveHint}=await import('../js/pages/upload.js');
  for(const status of ['hashing','checking','created','uploading','preparing','sending','completing']){
    const hint=leaveHint({status,busy:false,quick:true,withAi:true});
    assert.equal(hint.tone,'alert');assert.match(hint.text,/머물러 주세요/);
  }
  for(const status of ['uncertain','uploadUncertain']){
    const hint=leaveHint({status,busy:false,quick:true,withAi:true});
    assert.equal(hint.tone,'alert');assert.match(hint.text,/떠나지 말고/);
  }
  const done=leaveHint({status:'verified',busy:true,quick:true,withAi:true});
  assert.equal(done.tone,'notice');assert.match(done.text,/이동하거나 창을 닫아도 돼요/);assert.match(done.text,/서버에서 계속 진행/);
  assert.doesNotMatch(leaveHint({status:'verified',busy:false,quick:true,withAi:false}).text,/학습 자료/);
  assert.equal(leaveHint({status:'selected',busy:true,quick:true,withAi:true}).tone,'alert');
  for(const status of ['empty','selected','failed','expired']){
    const hint=leaveHint({status,busy:false,quick:true,withAi:true});
    assert.equal(hint.tone,'hint');assert.doesNotMatch(hint.text,/미확정|미확인/);
  }
});
test('제품 화면은 등록 준비 중에는 머물라는 안내를, 선택 전에는 일반 안내를 보인다',async(t)=>{
  t.mock.method(api,'createRemoteUploadIntent',async()=>response);
  const v=view();page({view:v});const node=s=>v.querySelector(s);
  assert.equal(node('[data-leave-hint]').className,'hint');
  node('[data-file]').files=[file];await node('[data-file]').events.change();
  node('[data-agree]').checked=true;await node('[data-create]').events.click();
  assert.equal(node('[data-leave-hint]').className,'alert');
  assert.match(node('[data-leave-hint]').textContent,/머물러 주세요/);
  listeners.get('auth:logout')();
});
