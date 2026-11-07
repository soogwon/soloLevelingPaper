import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { mountQuestionCard } from './question.js';

function card(call) {
  const doc = new JSDOM(readFileSync(new URL('./question-template.html', import.meta.url), 'utf8')).window.document;
  const app = { connect: async () => {}, callServerTool: call };
  mountQuestionCard(app, doc);
  return {app, el: id => doc.getElementById(id),
    open: (context_id = 'ctx') => app.ontoolresult({structuredContent: {context_id}})};
}
const ok = structuredContent => ({structuredContent});
async function submit(c) {
  c.el('question').value = '합성 질문';
  c.el('question').oninput();
  await c.el('submit').onclick();
}

test('키 보관 실패 시 제출하지 않는다', async () => {
  const calls = [];
  const c = card(async ({name}) => {
    calls.push(name);
    if (name === 'list_card_question_keys') return ok({requests: []});
    throw Error();
  });
  await c.open();
  await submit(c);
  assert.deepEqual(calls, ['list_card_question_keys', 'reserve_card_question_key']);
});

test('첫 응답 유실 뒤 새 카드는 보관한 키로 조회만 한다', async () => {
  const calls = [], records = [];
  let lookupFailed = true;
  const server = async ({name, arguments: args}) => {
    calls.push(name);
    assert.equal(args.context_id, 'ctx');
    switch (name) {
      case 'list_card_question_keys': return ok({requests: records});
      case 'reserve_card_question_key':
        records.push({request_key: 'saved', created_at: 100});
        return ok({request_key: 'saved'});
      case 'submit_paper_question':
        assert.equal(args.request_key, 'saved');
        throw Error('첫 응답 유실');
      case 'get_paper_question_status':
        assert.equal(args.request_key, 'saved');
        if (lookupFailed) throw Error('조회 오류');
        return ok({state: 'completed', answer: {status: 'ok', citations: [], answer_ko: '기존 답변'}});
      default: assert.fail(name);
    }
  };
  const first = card(server);
  await first.open();
  await submit(first);
  const second = card(server);
  await second.open();
  assert.match(second.el('result').textContent, /확인하지 못했습니다/);
  lookupFailed = false;
  assert.equal(second.el('restore').disabled, false);
  await second.el('restore').onclick();
  assert.match(second.el('result').textContent, /기존 답변/);
  assert.equal(calls.filter(n => n === 'submit_paper_question').length, 1);
  assert.equal(calls.filter(n => n === 'reserve_card_question_key').length, 1);
});

for (const event of ['새 입력', '취소']) {
  test(event + ' 뒤에는 지연된 이전 키로 제출하거나 이전 답변을 복원하지 않는다', async () => {
    const calls = [];
    let finishReservation;
    const c = card(async ({name, arguments: args}) => {
      calls.push([name, args.context_id]);
      if (name === 'list_card_question_keys') return ok({requests: [{request_key: 'key', created_at: 100}]});
      if (name === 'get_paper_question_status') return ok({state: 'completed',
        answer: {status: 'ok', answer_ko: '저장 답변', citations: []}});
      if (name === 'reserve_card_question_key' && args.context_id === 'old')
        return new Promise(resolve => { finishReservation = resolve; });
      if (name === 'reserve_card_question_key') return ok({request_key: 'new-key'});
      return ok({state: 'in_progress'});
    });
    const newInput = () => c.app.ontoolinput?.({arguments: {context_id: 'new'}});
    await c.open('old');
    const pending = submit(c);
    assert.equal(typeof finishReservation, 'function');
    if (event === '취소') c.app.ontoolcancelled?.({});
    else newInput();
    const count = calls.length;
    finishReservation(ok({request_key: 'old-key'}));
    await pending;
    await c.open('old');
    assert.equal(c.el('question').value, '');
    assert.equal(c.el('requests').options.length, 0);
    assert.doesNotMatch(c.el('result').textContent, /저장 답변/);
    for (const id of ['question', 'submit', 'restore']) assert.equal(c.el(id).disabled, true);
    await submit(c);
    c.el('restore').click();
    assert.equal(calls.length, count);
    if (event === '취소') newInput();
    await c.open('new');
    await submit(c);
    assert.deepEqual(calls.filter(([name]) => name === 'submit_paper_question'),
      [['submit_paper_question', 'new']]);
  });
}
