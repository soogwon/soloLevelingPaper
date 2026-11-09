import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { mountRecovery } from './recovery.js';

const html = await readFile(new URL('./recovery-template.html', import.meta.url), 'utf8');
const input = {version_id: 'v', base_embedding_set_id: 'e', request_key: 'key'};
const preview = {structuredContent: {...input, status: 'AVAILABLE', notice: '원문 전송·비용', target_count: 2}, _meta: {confirmation_token: 'token'}};
function setup(storage = new Map(), response) {
  const doc = new JSDOM(html).window.document, calls = [];
  const local = {getItem: k => storage.get(k) ?? null, setItem: (k,v) => storage.set(k,v), removeItem: k => storage.delete(k)};
  const app = {connect: async () => {}, callServerTool: async call => {
    calls.push(call);
    if (response) return response(call);
    return {structuredContent: call.name === 'cancel_translation_recovery' ? {status: 'cancelled'}
      : {...input, status: 'ACCEPTED', job_id: 'j'}};
  }};
  mountRecovery(app, doc, local);
  app.ontoolresult(preview);
  const el = id => doc.getElementById(id);
  const confirm = async () => { el('agree').checked = true; el('agree').onchange(); await el('confirm').onclick(); };
  return {app, doc, calls, storage, local, el, confirm};
}

test('동의·클릭 전 호출이 없고 키를 먼저 보관하며 중복 접수를 막는다', async () => {
  const s = setup();
  assert.equal(s.calls.length, 0);
  await s.el('confirm').onclick();
  assert.equal(s.calls.length, 0);
  await s.confirm(); await s.confirm();
  assert.deepEqual(s.calls.map(x => x.name), ['recover_translation']);
  const saved = JSON.parse([...s.storage.values()][0]);
  assert.deepEqual(saved, input);
  assert.equal(saved.confirmation_token, undefined);
  assert.equal(s.el('refresh').disabled, false);
});

test('취소는 복구 도구를 호출하지 않고 키도 저장하지 않는다', async () => {
  const s = setup(); await s.el('cancel').onclick(); await s.confirm();
  assert.deepEqual(s.calls.map(x => x.name), ['cancel_translation_recovery']);
  assert.equal(s.storage.size, 0);
});

test('키 보관 실패 시 서버에 접수하지 않는다', async () => {
  const s = setup();
  s.local.setItem = () => { throw new Error('private'); };
  await s.confirm();
  assert.equal(s.calls.length, 0);
  assert.match(s.el('status').textContent, /보관하지 못해/);
  assert.doesNotMatch(s.el('status').textContent, /private/);
});

test('응답 유실 후 새 카드에서도 같은 키의 조회만 명시적으로 수행한다', async () => {
  const storage = new Map();
  const first = setup(storage, () => { throw new Error('private'); });
  await first.confirm(); await first.confirm();
  assert.equal(first.calls.length, 1);
  const second = setup(storage);
  assert.equal(second.calls.length, 0);
  assert.equal(second.el('confirm').disabled, true);
  await second.confirm();
  assert.equal(second.calls.length, 0);
  await second.el('refresh').onclick();
  assert.deepEqual(second.calls, [{name: 'get_translation_recovery', arguments: input}]);
});

for (const [outcome, count, remaining, message] of [['FULL',2,0,'전체'], ['PARTIAL',1,1,'부분'], ['NO_PROGRESS',0,2,'성공 0개']]) {
  test(`${outcome} 결과를 구분하고 새 context를 만들지 않는다`, async () => {
    const s = setup(new Map(), call => ({structuredContent: {...input, job_id:'j', status: call.name === 'recover_translation' ? 'ACCEPTED' : outcome === 'NO_PROGRESS' ? 'FAILED' : 'COMPLETED',
      outcome, recovered_count:count, remaining_failed_count:remaining}}));
    await s.confirm(); await s.el('refresh').onclick();
    assert.match(s.el('status').textContent, new RegExp(message));
    assert.equal(s.storage.size, 0);
    assert.deepEqual(s.calls.map(x => x.name), ['recover_translation','get_translation_recovery']);
  });
}

test('조회 실패·상태 불명은 키를 유지하고 추가 실행하지 않는다', async () => {
  const s = setup(new Map(), call => {
    if (call.name === 'get_translation_recovery') throw new Error('private');
    return {structuredContent: {...input,status:'UNKNOWN'}};
  });
  await s.confirm(); await s.el('refresh').onclick(); await s.confirm();
  assert.equal(s.storage.size, 1);
  assert.equal(s.calls.filter(x => x.name === 'recover_translation').length, 1);
  assert.doesNotMatch(s.el('status').textContent,/private/);
});

test('다른 입력이 와도 이전 보관 키로만 조회하고 새 접수를 막는다', async () => {
  const s = setup(); await s.confirm();
  s.app.ontoolresult({structuredContent: {...preview.structuredContent,version_id:'other'},_meta:preview._meta});
  await s.confirm(); await s.el('refresh').onclick();
  assert.deepEqual(s.calls.map(x => x.name), ['recover_translation','get_translation_recovery']);
  assert.deepEqual(s.calls[1].arguments,input);
});

test('늦은 응답이 바뀐 카드 상태를 덮어쓰지 않는다', async () => {
  let release;
  const s = setup(new Map(), () => new Promise(resolve => { release = resolve; }));
  s.el('agree').checked = true; s.el('agree').onchange();
  const pending = s.el('confirm').onclick();
  s.app.ontoolresult({isError:true});
  const status = s.el('status').textContent;
  release({structuredContent:{...input,status:'COMPLETED',outcome:'FULL',recovered_count:2,remaining_failed_count:0}});
  await pending;
  assert.equal(s.el('status').textContent,status);
  assert.equal(s.el('confirm').disabled,true);
});

test('본문은 텍스트로만 표시하며 토큰 없음·잘못된 보관 데이터는 접수하지 않는다', async () => {
  const s = setup();
  s.app.ontoolresult({structuredContent:{...preview.structuredContent,notice:'<img src=x onerror=alert(1)>'}});
  assert.equal(s.doc.querySelector('img'),null);
  await s.confirm(); assert.equal(s.calls.length,0);
  s.local.getItem = () => 'not json';
  s.app.ontoolresult(preview);
  await s.confirm(); assert.equal(s.calls.length,0);
});

test('클릭 직전 다른 카드가 보관한 요청을 덮어쓰지 않는다', async () => {
  const storage = new Map(), first = setup(storage), second = setup(storage);
  await first.confirm(); await second.confirm();
  assert.equal(first.calls.length,1);
  assert.equal(second.calls.length,0);
  await second.el('refresh').onclick();
  assert.deepEqual(second.calls,[{name:'get_translation_recovery',arguments:input}]);
});

test('모순된 전체 복구 결과와 다른 범위 응답은 키를 지우지 않는다', async () => {
  for (const output of [{...input,status:'COMPLETED',outcome:'FULL',recovered_count:1,remaining_failed_count:1},
    {...input,version_id:'other',status:'COMPLETED',outcome:'FULL',recovered_count:2,remaining_failed_count:0},
    {...input,status:'BASE_CHANGED',job_id:'j',result_available:true}]) {
    const s = setup(new Map(), () => ({structuredContent:output}));
    await s.confirm(); await s.el('refresh').onclick();
    assert.equal(s.storage.size,1);
    assert.match(s.el('status').textContent,/확인하지 못했습니다/);
    assert.equal(s.calls.filter(c => c.name==='recover_translation').length,1);
  }
});

for (const status of ['BASE_CHANGED','NO_TARGET','REQUEST_CONFLICT']) {
  test(`접수 거부 ${status} 뒤에는 새 동의 안내를 받을 수 있다`, async () => {
    const s = setup(new Map(), () => ({structuredContent:{...input,status,job_id:null,result_available:false}}));
    await s.confirm();
    assert.equal(s.storage.size,0);
    assert.equal(s.el('confirm').disabled,true);
    assert.equal(s.el('refresh').disabled,true);
    assert.equal(s.calls.length,1);
    s.app.ontoolresult({...preview,structuredContent:{...preview.structuredContent,request_key:'new-key'}});
    assert.equal(s.el('agree').disabled,false);
    assert.equal(s.el('confirm').disabled,true);
  });
}

for (const event of ['ontoolinput', 'ontoolinputpartial', 'ontoolcancelled']) {
  test(`${event} 후 이전 동의와 늦은 준비 결과로 접수할 수 없다`, async () => {
    const s = setup();
    const next = {...input, version_id:'other', request_key:'other-key'};
    s.el('agree').checked = true;
    s.el('agree').onchange();
    s.app[event]({arguments:next});
    assert.equal(s.el('agree').checked, false);
    s.app.ontoolresult(preview);
    await s.confirm();
    assert.deepEqual(s.calls, []);
    s.app.ontoolinput({arguments:next});
    s.app.ontoolresult({...preview, structuredContent:{...preview.structuredContent, ...next}});
    assert.equal(s.el('confirm').disabled, true);
    await s.confirm();
    assert.equal(s.calls[0].arguments.request_key, 'other-key');
  });
}

test('다른 작업 진행 중으로 미접수된 키만 해제한다', async () => {
  for (const [job_id, result_available, retained] of [[null,false,0], ['active',false,1], [null,true,1], [undefined,false,1]]) {
    const s = setup(new Map(), () => ({structuredContent:{...input, status:'IN_PROGRESS', job_id, result_available}}));
    await s.confirm();
    assert.equal(s.storage.size, retained);
    if (!retained) {
      assert.match(s.el('status').textContent, /접수되지 않았습니다/);
      await s.el('refresh').onclick();
      assert.equal(s.calls.length, 1);
      s.app.ontoolresult(preview);
      assert.equal(s.el('agree').disabled, false);
      assert.equal(s.el('confirm').disabled, true);
    }
  }
});

test('제출 중 호스트 취소 후 늦은 응답이 화면을 복원하지 않고 보관 키로만 재조회한다', async () => {
  const storage = new Map();
  let finish;
  const first = setup(storage, () => new Promise(resolve => { finish = resolve; }));
  const request = first.confirm();
  first.app.ontoolcancelled({});
  const status = first.el('status').textContent;
  finish({structuredContent:{...input,status:'ACCEPTED',job_id:'job'}});
  await request;
  assert.equal(first.el('status').textContent, status);
  assert.equal(first.el('confirm').disabled, true);
  assert.equal(storage.size, 1);
  const reopened = setup(storage);
  await reopened.confirm();
  await reopened.el('refresh').onclick();
  assert.deepEqual(reopened.calls, [{name:'get_translation_recovery', arguments:input}]);
});
