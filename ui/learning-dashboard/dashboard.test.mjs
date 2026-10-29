import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { mountDashboard } from './dashboard.js';

const html = await readFile(new URL('./template.html', import.meta.url), 'utf8');
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const result = (changes = {}) => ({ structuredContent: {
  context_id: 'ctx', version_id: 'v1', paper_id: 'p1', title: '테스트 논문', file_display_name: 'paper.pdf', goal: 'understand',
  processing: { job_id: 'j1', status: 'ready', stage: 'index', limitations: [] },
  progress: { recorded: false, current_step: 'preview', completed_steps: [], total_steps: 4, completion_ratio: 0, last_studied_at: null },
  summary: null,
  quiz_scores: ['preview', 'structure', 'deep_read', 'review'].map((step) => ({
    step, attempt_count: 0, latest_attempt_id: null, latest_correct: null, latest_total: null, latest_score: null, best_score: null,
  })), ...changes,
} });
async function setup(overrides = {}, timeout = 100) {
  const doc = new JSDOM(html).window.document;
  const calls = [];
  const app = { connect: async () => {}, callServerTool: async (args) => { calls.push(args); return result(); }, ...overrides };
  mountDashboard(app, doc, timeout); await tick();
  return { app, doc, calls, get: (id) => doc.getElementById(id) };
}

test('없는 기록과 저장된 0점을 구분하고 모든 텍스트를 안전하게 표시한다', async () => {
  const { app, get, doc } = await setup();
  app.ontoolresult(result());
  assert.equal(get('details').hidden, false);
  assert.match(get('progress-label').textContent, /진행 기록 없음/);
  assert.match(get('quiz').textContent, /퀴즈 기록 없음/);
  const data = result().structuredContent;
  data.title = '<img src=x onerror="alert(1)">';
  data.progress = { ...data.progress, recorded: true, completed_steps: ['preview'], completion_ratio: .25, current_step: 'structure' };
  data.summary = { summary_id: 's', created_at: 'now', items: [{ kind: 'claim', text: '<script>bad()</script>', evidence_ids: ['e1'] }] };
  data.quiz_scores[0] = { ...data.quiz_scores[0], attempt_count: 1, latest_attempt_id: 'a', latest_correct: 0, latest_total: 3, latest_score: 0, best_score: 0 };
  app.ontoolresult({ structuredContent: data });
  assert.equal(doc.querySelector('img, script'), null);
  assert.match(get('progress-label').textContent, /25%/);
  assert.match(get('steps').textContent, /구조 파악 · 현재 단계/);
  assert.match(get('quiz').textContent, /0 \/ 3문항 \(0%\)/);
  assert.match(get('summary-list').textContent, /e1/);
  assert.equal(get('summary-empty').hidden, true);
});

test('오류·형식 오류·다른 맥락 응답에서 이전 데이터를 숨긴다', async () => {
  const { app, get, doc } = await setup();
  app.ontoolinput({ arguments: { context_id: 'ctx' } });
  app.ontoolresult(result());
  app.ontoolresult(result({ context_id: 'other' }));
  assert.equal(get('details').hidden, true);
  app.ontoolresult({ isError: true, content: [{ text: 'secret-key' }] });
  assert.equal(doc.body.textContent.includes('secret-key'), false);
  for (const data of [null, {}, result({ quiz_scores: [] }).structuredContent, result({ summary: {} }).structuredContent]) {
    app.ontoolresult({ structuredContent: data });
    assert.equal(get('details').hidden, true);
  }
});

test('새로고침은 같은 맥락의 조회만 호출하고 늦은 응답은 새 입력을 덮지 않는다', async () => {
  let finish;
  const calls = [];
  const { app, get } = await setup({ callServerTool: (args) => { calls.push(args); return new Promise((r) => { finish = r; }); } });
  app.ontoolresult(result());
  get('refresh').click(); get('refresh').click();
  assert.deepEqual(calls, [{ name: 'get_learning_dashboard', arguments: { context_id: 'ctx' } }]);
  app.ontoolinput({ arguments: { context_id: 'new' } });
  app.ontoolresult(result({ context_id: 'new' }));
  finish(result()); await tick();
  assert.match(get('identity').textContent, /^new/);
});

test('연결 실패와 취소를 표시하고 민감한 오류 전문은 노출하지 않는다', async () => {
  const { app, get, doc } = await setup({ connect: async () => { throw Error('secret'); } });
  assert.match(get('message').textContent, /연결하지 못/);
  assert.equal(get('refresh').disabled, true);
  assert.equal(doc.body.textContent.includes('secret'), false);
  app.ontoolcancelled();
  assert.match(get('message').textContent, /취소/);
});

test('새로고침 시간 초과 후 다시 시도할 수 있고 늦은 결과는 표시하지 않는다', async () => {
  let finish;
  const { app, get } = await setup({ callServerTool: () => new Promise((r) => { finish = r; }) }, 15);
  app.ontoolresult(result()); get('refresh').click();
  await new Promise((r) => setTimeout(r, 25));
  assert.match(get('message').textContent, /지연되거나 실패/);
  assert.equal(get('refresh').disabled, false);
  finish(result({ title: '늦은 응답' })); await tick();
  assert.equal(get('details').hidden, true);
});
