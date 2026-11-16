import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { prepareAnnotationRequest } from './annotation-request.js';
import { createRemoteAnnotationRecovery } from './remote-annotation-request.js';

const input = {context_id:'ctx',kind:'note',body:'비공개 메모',creation_origin:'unknown',segments:[]};
function harness() {
  const values = new Map();
  const storage = {getItem:k=>values.get(k) ?? null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
  let current = {service:'server-a',owner:'owner-a',session:'login-1'};
  const queues = new Map();
  // 호스트 Web Locks의 같은 이름 직렬화를 대역으로 재현한다.
  const locks = {request(name, task) {
    const next=(queues.get(name) ?? Promise.resolve()).then(task);
    queues.set(name,next.catch(()=>{}));return next;
  }};
  const options = {storage,crypto:webcrypto,locks,currentScope:()=>current};
  const make = ()=>createRemoteAnnotationRecovery(options);
  const change = update=>{current={...current,...update};};
  return {values,storage,options,make,change};
}
const key = ticket=>ticket.send(async request=>request.request_key);

test('새 요청 확인 없이 키를 만들지 않으며 저장 공간 유실에도 자동 재발급하지 않는다',async()=>{
  const h=harness();assert.equal(await h.make().prepare(input),null);assert.equal(h.values.size,0);
  await h.make().prepare(input,{allowNew:true});h.values.clear();
  assert.equal(await h.make().prepare(input),null);
});
test('응답 유실과 클라이언트 재생성 후 같은 키로 현재 결과를 재생한다',async()=>{
  const h=harness();const records=new Map();let receivedKey;
  const first=await h.make().prepare(input,{allowNew:true});
  await assert.rejects(first.send(async r=>{receivedKey=r.request_key;records.set(receivedKey,{id:'one',revision:1});throw Error('lost');}));
  records.set(receivedKey,{id:'one',revision:2});
  h.change({session:'login-2'});
  const retry=await h.make().prepare({...input});
  const result=await retry.send(async r=>records.get(r.request_key));
  assert.deepEqual(result,{id:'one',revision:2});assert.equal(records.size,1);
  assert.equal(await key(retry),receivedKey);
  retry.confirm();assert.equal(await h.make().prepare(input),null);
});
test('동시 카드 20개의 키 준비는 하나의 키만 저장한다',async()=>{
  const h=harness();const tickets=await Promise.all(Array.from({length:20},()=>h.make().prepare(input,{allowNew:true})));
  assert.equal(new Set(await Promise.all(tickets.map(key))).size,1);assert.equal(h.values.size,1);
});
test('서비스·계정·맥락은 분리하고 객체 필드 순서 변경은 같은 키로 복구한다',async()=>{
  const h=harness();const first=await key(await h.make().prepare(input,{allowNew:true}));
  assert.equal(await key(await h.make().prepare(Object.fromEntries(Object.entries(input).reverse()))),first);
  for(const update of [{owner:'owner-b'},{service:'server-b'}]) {
    h.change(update);assert.equal(await h.make().prepare(input),null);
    assert.notEqual(await key(await h.make().prepare(input,{allowNew:true})),first);
  }
  assert.equal(await h.make().prepare({...input,context_id:'other'}),null);
  assert.equal(await h.make().prepare({...input,body:'다른 입력'}),null);
});
test('원격 범위에 옛 unknown 생략 요청을 복구하며 비범위 로컬 키는 사용하지 않는다',async()=>{
  const h=harness();
  await prepareAnnotationRequest(input,h.storage,webcrypto);
  assert.equal(await h.make().prepare(input),null);
  const old={...input};delete old.creation_origin;
  const first=await h.make().prepare(old,{allowNew:true});const saved=await key(first);
  const retry=await h.make().prepare(input);
  await retry.send(async r=>{assert.equal(r.request_key,saved);assert.equal(Object.hasOwn(r,'creation_origin'),false);});
  const serialized=JSON.stringify([...h.values]);
  for(const sensitive of ['비공개 메모','owner-a','server-a','ctx']) assert.ok(!serialized.includes(sensitive));
});
test('계정 또는 같은 계정의 세션 변경 후 이전 요청 전송·확정을 거부한다',async()=>{
  for(const change of [{owner:'owner-b'},{session:'login-2'}]) {
    const h=harness();const pending=await h.make().prepare(input,{allowNew:true});h.change(change);
    let calls=0;await assert.rejects(pending.send(async()=>calls++));assert.equal(calls,0);
    assert.throws(()=>pending.confirm());assert.equal(h.values.size,1);
  }
});
test('해시 계산 중 계정 변경은 키 저장 전에 차단한다',async()=>{
  const h=harness();h.options.crypto={...webcrypto,subtle:{digest:async(...args)=>{h.change({owner:'other'});return webcrypto.subtle.digest(...args);}}};
  await assert.rejects(h.make().prepare(input,{allowNew:true}),/ANNOTATION_RECOVERY_UNAVAILABLE/);
  assert.equal(h.values.size,0);
});
test('응답 도중 계정 변경은 결과를 버리고 이전 키를 유지한다',async()=>{
  const h=harness();const pending=await h.make().prepare(input,{allowNew:true});
  await assert.rejects(pending.send(async()=>{h.change({owner:'other'});return {id:'private'};}));
  assert.equal(h.values.size,1);
});
test('저장 실패·잠금 부재·범위 누락 시 전송 가능한 요청을 반환하지 않는다',async()=>{
  for(const patch of [{locks:null},{currentScope:()=>null},{storage:{getItem:()=>null,setItem:()=>{throw Error('secret');}}}]) {
    const h=harness();Object.assign(h.options,patch);
    await assert.rejects(h.make().prepare(input,{allowNew:true}),/^Error: ANNOTATION_RECOVERY_UNAVAILABLE$/);
  }
});
test('이미 확인한 요청과 변조된 보관 키로 전송하지 않는다',async()=>{
  const h=harness();const pending=await h.make().prepare(input,{allowNew:true});pending.confirm();
  await assert.rejects(pending.send(async()=>assert.fail('전송 금지')));
  const next=await h.make().prepare(input,{allowNew:true});h.values.set([...h.values.keys()][0],'!bad');
  await assert.rejects(next.send(async()=>assert.fail('전송 금지')));
});

test('삭제·충돌 응답을 새 키 발급이나 성공 확정으로 처리하지 않는다',async()=>{
  const h=harness();const ticket=await h.make().prepare(input,{allowNew:true});const original=await key(ticket);
  for(const code of ['ANNOTATION_DELETED','REQUEST_KEY_CONFLICT']) {
    assert.deepEqual(await ticket.send(async()=>({isError:true,code})),{isError:true,code});
    assert.equal(await key(await h.make().prepare(input)),original);
  }
});
test('전송 오류 원문과 저장소 오류는 고정 오류로 바꾼다',async()=>{
  const h=harness();const ticket=await h.make().prepare(input,{allowNew:true});
  await assert.rejects(ticket.send(async()=>{throw Error('private body');}),/^Error: ANNOTATION_RECOVERY_UNAVAILABLE$/);
});

test('성공 응답의 늦은 중복 확정은 새 요청 키를 지우지 않는다',async()=>{
  const h=harness();const old=await h.make().prepare(input,{allowNew:true});
  const oldKey=await key(old);old.confirm();
  const next=await h.make().prepare(input,{allowNew:true});const nextKey=await key(next);
  assert.notEqual(nextKey,oldKey);old.confirm();
  assert.equal(await key(await h.make().prepare(input)),nextKey);
});
test('조용히 실패하는 저장소도 키 읽기 검증으로 거부한다',async()=>{
  const h=harness();h.options.storage={getItem:()=>null,setItem:()=>{}};
  await assert.rejects(h.make().prepare(input,{allowNew:true}),/ANNOTATION_RECOVERY_UNAVAILABLE/);
});
test('JSON 직렬화에서 입력이 달라지는 빈 배열 칸과 undefined는 거부한다',async()=>{
  for (const segments of [Array(1),[undefined]]) {
    const h=harness();await assert.rejects(h.make().prepare({...input,segments},{allowNew:true}));
    assert.equal(h.values.size,0);
  }
});
