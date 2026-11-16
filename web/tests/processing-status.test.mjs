import test from 'node:test';
import assert from 'node:assert/strict';
import {createProcessingStatus, processingMessages} from '../js/lib/processing-status.js';
const id = '11111111-1111-4111-8111-111111111111';
const other = '22222222-2222-4222-8222-222222222222';
const target = {intent_id:id,paper_id:id,version_id:id};
const result = {job_id:id,paper_id:id,version_id:id,status:'queued'};
for (const status of ['queued','running','succeeded','failed','cancelled']) {
  test(`공개 상태 ${status}를 조회하고 고정 안내만 표시한다`, async () => {
    const calls = [];
    const c = createProcessingStatus({api:{getRemoteProcessing:async key => {calls.push(key);return {...result,status,detail:'secret'};}}});
    c.bind(target); await c.refresh();
    assert.deepEqual(calls,[id]); assert.equal(c.snapshot().status,status);
    assert.equal(c.snapshot().job.detail,undefined); assert.ok(processingMessages[status]);
  });
}
for (const bad of [{status:'prepared'},{status:'translation_incomplete'},{status:'completed'},{paper_id:other},{version_id:other},{job_id:'bad'}]) {
  test(`내부 상태 또는 다른 출처는 완료로 표시하지 않는다: ${JSON.stringify(bad)}`,async()=>{
    const c=createProcessingStatus({api:{getRemoteProcessing:async()=>({...result,...bad})}});
    c.bind(target);await c.refresh();assert.equal(c.snapshot().status,'invalid');assert.equal(c.snapshot().job,null);
  });
}
for (const action of ['close','replace','clear']) test(`${action} 후 늦은 응답을 폐기한다`,async()=>{
  let resolve;const pending=new Promise(r=>resolve=r);
  const c=createProcessingStatus({api:{getRemoteProcessing:()=>pending}});
  c.bind(target);const request=c.refresh();
  if(action==='close')c.close();else c.bind(action==='clear'?null:{...target,intent_id:other});
  resolve(result);await request;assert.equal(c.snapshot().job,null);assert.equal(c.snapshot().busy,false);
});
test('오류 뒤에도 같은 작업 ID만 허용하고 재조회는 GET 포트만 호출한다',async()=>{
  let value=result, failure=false, calls=0;
  const c=createProcessingStatus({api:{getRemoteProcessing:async()=>{calls++;if(failure)throw {status:503,message:'secret'};return value;}}});
  c.bind(target);await c.refresh();failure=true;await c.refresh();
  assert.equal(c.snapshot().status,'uncertain');assert.equal(c.snapshot().job,null);
  failure=false;value={...result,job_id:other};await c.refresh();assert.equal(c.snapshot().status,'invalid');
  value=result;await c.refresh();assert.equal(c.snapshot().job.job_id,id);assert.equal(calls,4);
});
for(const code of [401,403,404])test(`${code}를 미접수로 단정하지 않는다`,async()=>{
  const c=createProcessingStatus({api:{getRemoteProcessing:async()=>{throw {status:code};}}});
  c.bind(target);await c.refresh();assert.equal(c.snapshot().status,'unavailable');
});
test('중복 클릭은 조회를 한 번만 보내며 같은 출처 재바인딩은 응답을 버리지 않는다',async()=>{
  let resolve,calls=0;const pending=new Promise(r=>resolve=r);
  const c=createProcessingStatus({api:{getRemoteProcessing:()=>{calls++;return pending;}}});
  c.bind(target);const first=c.refresh();await c.refresh();c.bind({...target});resolve(result);await first;
  assert.equal(calls,1);assert.equal(c.snapshot().job.job_id,id);
});
test('UUID처럼 문자열로 변환되는 배열을 출처나 작업 ID로 받지 않는다',async()=>{
  let calls=0;
  const c=createProcessingStatus({api:{getRemoteProcessing:async()=>{calls++;return {...result,job_id:[id]};}}});
  c.bind({...target,intent_id:[id]});await c.refresh();assert.equal(calls,0);assert.equal(c.snapshot().canRead,false);
  c.bind(target);await c.refresh();assert.equal(c.snapshot().status,'invalid');assert.equal(c.snapshot().job,null);
});
test('이전 파일의 늦은 실패가 새 파일 조회의 성공을 덮지 않는다',async()=>{
  let reject;
  const pending=new Promise((_,r)=>reject=r);
  const c=createProcessingStatus({api:{getRemoteProcessing:key=>key===id?pending:Promise.resolve({...result,job_id:other})}});
  c.bind(target);const first=c.refresh();c.bind({...target,intent_id:other});await c.refresh();
  reject({status:503,message:'secret'});await first;
  assert.equal(c.snapshot().job.job_id,other);assert.equal(c.snapshot().status,'queued');assert.equal(c.snapshot().busy,false);
});
