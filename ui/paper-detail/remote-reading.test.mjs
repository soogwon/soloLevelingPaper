import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {JSDOM} from 'jsdom';
import {mountRemoteReading} from './remote-reading.js';

const html = readFileSync(new URL('./remote-reading-template.html', import.meta.url), 'utf8');
const context = '00000000-0000-4000-8000-000000000591';
const key = '00000000-0000-4000-8000-000000000001';
const binding = 'a'.repeat(64);
const lookup = 'show_remote_reading_progress';
const save = 'save_remote_reading_progress_card';
const ok = result => ({structuredContent: {status: 'ok', result}});
const status = status => ({structuredContent: {status}});
const observation = (reading = 'reading', revision = 1, page = 2, writable = true) => ok({
  binding, can_write: writable, state: {context_id: context, title: '<합성 논문>', processing_status: 'published',
    reading_status: reading, revision, last_position: page == null ? null : {pdf_page: page}},
});
const committed = revision => ok({state: {context_id: context}, applied_revision: revision});

function setup(respond, initial = observation()) {
  const dom = new JSDOM(html);
  const calls = [];
  const app = {connect: async () => {}, callServerTool: async call => {
    calls.push(call);
    return respond(call, calls.length);
  }};
  const doc = dom.window.document;
  let serial = 0;
  mountRemoteReading(app, doc, () => key.slice(0, -1) + (++serial));
  const el = id => doc.getElementById(id);
  app.ontoolinput({arguments: {context_id: context}});
  app.ontoolresult(initial);
  return {el, app, calls, close: () => dom.window.dispatchEvent(new dom.window.Event('pagehide'))};
}

test('완료와 재열기는 명시 클릭으로만 저장하고 최근 위치를 유지한다', async () => {
  const {el, calls} = setup((call, n) => call.name === save ? committed(n + 1)
    : observation(n === 2 ? 'completed' : 'reading', n === 2 ? 2 : 3));
  assert.deepEqual(calls, []);
  assert.match(el('record').textContent, /게시 완료 · 읽기: 읽는 중/);
  assert.equal(el('title').textContent, '<합성 논문>');
  await el('complete').onclick();
  assert.deepEqual(calls[0], {name: save, arguments: {context_id: context, request_key: key,
    status: 'completed', pdf_page: 2, expected_revision: 1, expected_binding: binding}});
  assert.equal(calls[1].name, lookup);
  assert.equal(el('reopen').disabled, false);
  await el('reopen').onclick();
  assert.equal(calls[2].arguments.status, 'reading');
  assert.equal(calls[2].arguments.expected_revision, 2);
  assert.match(el('record').textContent, /PDF 2페이지/);
});

test('위치 저장은 현재 상태를 유지하고 빈칸은 위치만 지운다', async () => {
  const {el, calls} = setup(call => call.name === save ? committed(5) : observation('completed', 5, null),
    observation('completed', 4));
  el('page').value = '';
  await el('position').onsubmit({preventDefault() {}});
  assert.equal(calls[0].arguments.status, 'completed');
  assert.equal(calls[0].arguments.pdf_page, null);
  assert.match(el('record').textContent, /최근 위치: 없음/);
});

test('revision 충돌 시 기존 요청을 버리고 읽기 전용 조회 후 최신 revision만 사용한다', async () => {
  const {el, calls} = setup((call, n) => n === 1 ? status('conflict')
    : call.name === lookup ? observation('reading', 4, 5) : committed(5));
  await el('complete').onclick();
  assert.deepEqual(calls.map(call => call.name), [save, lookup]);
  assert.deepEqual(calls[1].arguments, {context_id: context, expected_binding: binding});
  assert.equal(el('page').value, '5');
  assert.match(el('status').textContent, /최신 상태/);
  await el('complete').onclick();
  assert.notEqual(calls[2].arguments.request_key, calls[0].arguments.request_key);
  assert.equal(calls[2].arguments.expected_revision, 4);
  assert.equal(calls[2].arguments.pdf_page, 5);
});

test('응답 유실은 자동 저장 없이 원래 입력과 키의 명시적 재시도만 허용한다', async () => {
  const {el, calls} = setup((call, n) => {
    if (n === 1) throw new Error();
    return call.name === save ? committed(2) : observation('completed', 2);
  });
  await el('complete').onclick();
  assert.equal(calls.length, 1);
  assert.equal(el('retry').hidden, false);
  assert.equal(el('save').disabled, true);
  el('page').value = '6';
  await el('retry').onclick();
  assert.deepEqual(calls[1], calls[0]);
  assert.equal(el('retry').hidden, true);
});

test('이전 카드 응답과 연결 변경 뒤 쓰기를 차단한다', async () => {
  let resolve;
  const {el, calls, close} = setup(() => new Promise(done => { resolve = done; }));
  const operation = el('complete').onclick();
  close();
  resolve(committed(2));
  await operation;
  assert.equal(calls.length, 1);
  assert.equal(el('record').textContent, '');
  assert.equal(el('retry').hidden, true);

  const changed = setup(() => ok({connection_changed: true}));
  await changed.el('complete').onclick();
  assert.equal(changed.el('record').textContent, '');
  assert.equal(changed.el('complete').disabled, true);
});

test('미시작과 읽기 전용 맥락에서는 상태 변경이 불가능하다', async () => {
  for (const initial of [observation('not_started', null, null), observation('reading', 1, 1, false)]) {
    const {el, calls} = setup(() => assert.fail('조회만으로 쓰기 호출'), initial);
    assert.equal(el('complete').disabled, true);
    assert.equal(el('reopen').disabled, true);
    assert.equal(el('page').disabled, true);
    await el('complete').onclick();
    assert.deepEqual(calls, []);
  }
});
