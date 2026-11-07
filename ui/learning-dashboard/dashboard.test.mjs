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

const detailResult = (changes = {}) => ({ structuredContent: {
  context_id: 'ctx', paper_id: 'p1', version_id: 'v1', parse_revision_id: 'parse',
  translation_revision_id: 'translation', scope: 'paper', file_display_name: 'paper.pdf',
  title: '상세 제목', section_ids: [], summary: null, evidence: [], ...changes,
} });

test('상세 조회는 같은 맥락의 저장 정보만 읽고 돌아갈 수 있다', async () => {
  const calls = [];
  const { app, get } = await setup({ callServerTool: async (args) => { calls.push(args); return detailResult(); } });
  app.ontoolresult(result());
  get('open-detail').click(); get('open-detail').click(); await tick();
  assert.deepEqual(calls, [{ name: 'get_paper_detail', arguments: { context_id: 'ctx', scope: 'paper', offset: 0, limit: 20 } }]);
  assert.equal(get('details').hidden, true);
  assert.equal(get('detail-view').hidden, false);
  assert.match(get('paper-detail-answer').textContent, /저장된 요약이 없습니다/);
  get('back-dashboard').click();
  assert.equal(get('details').hidden, false);
  assert.equal(get('detail-view').hidden, true);
});

test('저장 요약 ID를 고정하고 원문·번역·근거 출처를 표시한다', async () => {
  const calls = [];
  const evidence = { evidence_id: 'e1', chunk_id: 'c1', version_id: 'v1', parse_revision_id: 'parse',
    translation_revision_id: 'translation', quote_original: '<script>원문</script>', quote_ko: '번역', pdf_page: 2, printed_page_label: null };
  const summary = { summary_id: 's1', verification_level: 'structural_only', total_items: 1, next_offset: null,
    items: [{ kind: 'claim', text: '요약', evidence_ids: ['e1'] }] };
  const { app, get, doc } = await setup({ callServerTool: async (args) => {
    calls.push(args); return detailResult({ summary, evidence: [evidence] });
  } });
  app.ontoolresult(result({ summary: { ...summary, created_at: 'now' } }));
  get('open-detail').click(); await tick();
  assert.equal(calls[0].arguments.summary_id, 's1');
  assert.equal(get('paper-detail-original').textContent, '<script>원문</script>');
  assert.equal(get('paper-detail-translation').textContent, '번역');
  assert.match(get('paper-detail-location').textContent, /2페이지/);
  assert.equal(doc.querySelector('script'), null);
});

for (const [name, response] of [
  ['다른 맥락', detailResult({ context_id: 'other' })],
  ['다른 버전', detailResult({ version_id: 'other' })],
  ['다른 논문', detailResult({ paper_id: 'other' })],
  ['도구 오류', { isError: true, content: [{ text: 'private-error' }] }],
]) test(`${name} 상세 응답은 숨기고 일반 안내만 표시한다`, async () => {
  const { app, get, doc } = await setup({ callServerTool: async () => response });
  app.ontoolresult(result()); get('open-detail').click(); await tick();
  assert.equal(get('paper-detail-content').hidden, true);
  assert.match(get('message').textContent, /확인하지 못/);
  assert.equal(doc.body.textContent.includes('private-error'), false);
});

for (const action of ['back', 'input', 'cancel', 'timeout']) test(`상세 조회 중 ${action} 이후 늦은 결과를 무시한다`, async () => {
  let finish;
  const { app, get } = await setup({ callServerTool: () => new Promise((r) => { finish = r; }) }, 15);
  app.ontoolresult(result()); get('open-detail').click();
  if (action === 'back') get('back-dashboard').click();
  if (action === 'input') { app.ontoolinput({ arguments: { context_id: 'new' } }); app.ontoolresult(result({ context_id: 'new' })); }
  if (action === 'cancel') app.ontoolcancelled();
  if (action === 'timeout') await new Promise((r) => setTimeout(r, 25));
  finish(detailResult()); await tick();
  assert.equal(get('paper-detail-content').hidden, true);
  if (action !== 'timeout') assert.equal(get('detail-view').hidden, true);
  if (action === 'input') assert.match(get('identity').textContent, /^new/);
});

test('요약이 교체된 응답은 현재 대시보드의 근거로 표시하지 않는다', async () => {
  const { app, get } = await setup({ callServerTool: async () => detailResult() });
  app.ontoolresult(result({ summary: { summary_id: 'old', created_at: 'now',
    items: [{ kind: 'claim', text: '요약', evidence_ids: ['e1'] }] } }));
  get('open-detail').click(); await tick();
  assert.equal(get('paper-detail-content').hidden, true);
  assert.match(get('message').textContent, /확인하지 못/);
});

test('형식 오류와 예외는 원문 오류 대신 안전한 안내를 표시한다', async () => {
  for (const response of [() => detailResult({ evidence: [{}] }), () => { throw Error('private-error'); }]) {
    const { app, get, doc } = await setup({ callServerTool: async () => response() });
    app.ontoolresult(result()); get('open-detail').click(); await tick();
    assert.equal(get('paper-detail-content').hidden, true);
    assert.match(get('message').textContent, /확인하지 못/);
    assert.equal(doc.body.textContent.includes('private-error'), false);
  }
});

for (const fault of ['missing-summary', 'missing-parse', 'missing-translation', 'version', 'parse', 'translation', 'answer-shape']) {
  test(`상세 계약 ${fault} 오류는 다른 출처의 본문을 표시하지 않는다`, async () => {
    const evidence = { evidence_id: 'e1', chunk_id: 'c1', version_id: 'v1', parse_revision_id: 'parse',
      translation_revision_id: 'translation', quote_original: '표시되면 안 되는 합성 원문', quote_ko: '합성 번역', pdf_page: 1, printed_page_label: null };
    const response = detailResult({ evidence: [evidence] });
    const data = response.structuredContent;
    if (fault === 'missing-summary') { delete data.summary; evidence.version_id = 'other'; }
    if (fault === 'missing-parse') delete data.parse_revision_id;
    if (fault === 'missing-translation') delete data.translation_revision_id;
    if (fault === 'version') evidence.version_id = 'other';
    if (fault === 'parse') evidence.parse_revision_id = 'other';
    if (fault === 'translation') evidence.translation_revision_id = 'other';
    if (fault === 'answer-shape') Object.assign(data, { citations: [evidence], claims: [], answer_ko: '합성 답변', status: 'ok', verification_level: 'structural_only' });
    let calls = 0;
    const { app, get, doc } = await setup({ callServerTool: async () => ++calls === 1 ? response : detailResult() });
    app.ontoolresult(result()); get('open-detail').click(); await tick();
    assert.equal(get('paper-detail-content').hidden, true);
    assert.equal(get('paper-detail-original').textContent, '');
    assert.equal(doc.body.textContent.includes(evidence.quote_original), false);
    assert.match(get('message').textContent, /확인하지 못/);
    // 거부 이후 사용자가 다시 조회하면 정상 응답을 표시할 수 있다.
    get('back-dashboard').click(); get('open-detail').click(); await tick();
    assert.equal(get('paper-detail-content').hidden, false);
    assert.match(get('paper-detail-answer').textContent, /저장된 요약이 없습니다/);
    assert.equal(calls, 2);
  });
}

test('메모 미리보기는 텍스트로 표시하고 빈 목록에서는 이전 메모를 지운다', async () => {
  const { app, get } = await setup();
  app.ontoolresult(result({ notes: { count: 1, previews: ['<b>메모</b>'] } }));
  assert.equal(get('note-previews').textContent, '<b>메모</b>');
  assert.equal(get('note-previews').querySelector('b'), null);
  app.ontoolresult(result({ notes: { count: 0, previews: [] } }));
  assert.equal(get('note-count').textContent, '저장된 메모가 없습니다.');
  assert.equal(get('note-previews').children.length, 0);
});

test('상세에서 맥락 기록 20건과 다음 페이지를 순서대로 읽고 대시보드로 돌아간다', async () => {
  const notes = Array.from({ length: 21 }, (_, i) => ({
    annotation_id: String(i + 1), context_id: 'ctx', kind: 'note', body: `메모 ${i + 1}`, segments: [],
  }));
  const requests = [];
  const { app, get } = await setup({ callServerTool: async call => {
    requests.push(call);
    if (call.name === 'get_paper_detail') {
      return detailResult({ annotation_page: { context_id: 'ctx', items: notes.slice(0, 20), next_cursor: 'next' } });
    }
    return { structuredContent: { items: notes.slice(20), next_cursor: null } };
  }});
  app.ontoolresult(result());
  get('open-detail').click();
  await tick();
  const panel = get('paper-detail-context-records');
  const rows = () => Array.from(panel.querySelectorAll('li'), li => li.textContent);
  const expected = notes.map(note => `메모 · 학습 맥락에만 저장 · ${note.body}`);
  assert.equal(panel.hidden, false);
  assert.deepEqual(rows(), expected.slice(0, 20));
  assert.deepEqual(requests.map(call => call.name), ['get_paper_detail']);
  const more = panel.querySelector('button');
  assert.equal(more.hidden, false);
  more.click();
  await tick();
  assert.deepEqual(requests.slice(1), [{ name: 'list_annotations', arguments: { request: { context_id: 'ctx', cursor: 'next' } } }]);
  assert.deepEqual(rows(), expected);
  assert.equal(more.hidden, true);
  get('back-dashboard').click();
  assert.equal(get('detail-view').hidden, true);
  assert.equal(get('details').hidden, false);
  assert.match(get('identity').textContent, /^ctx/);
});
