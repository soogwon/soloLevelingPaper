import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { mountStatusApp } from './status.js';

const html = await readFile(new URL('./template.html', import.meta.url), 'utf8');
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const result = (changes = {}) => ({ structuredContent: {
  job_id: 'job-1', version_id: 'version-1', status: 'ready', stage: 'index',
  capabilities: ['start_learning'], limitations: [], result_available: true, ...changes,
} });

async function setup(overrides = {}, timeout = 1000) {
  const doc = new JSDOM(html).window.document;
  const calls = [];
  const app = { connect: async () => {}, callServerTool: async (args) => {
    calls.push(args);
    return result();
  }, ...overrides };
  mountStatusApp(app, doc, timeout);
  await tick();
  return { app, doc, calls, message: () => doc.getElementById('message').textContent };
}

test('초기 로딩과 정상 상태·제한 사항을 표시한다', async () => {
  const { app, doc, message } = await setup();
  assert.match(message(), /기다리고/);
  assert.equal(doc.getElementById('refresh').disabled, true);
  app.ontoolresult(result({ limitations: ['일부 번역 누락'] }));
  assert.match(message(), /완료/);
  assert.equal(doc.getElementById('details').hidden, false);
  assert.equal(doc.getElementById('stage').textContent, '검색 색인');
  assert.match(doc.getElementById('limitations').textContent, /일부 번역 누락/);
  assert.match(doc.getElementById('next').textContent, /전체 번역 완료를 뜻하지/);
});

test('빈 결과·잘못된 응답·오류를 구분하고 민감 오류 원문을 표시하지 않는다', async () => {
  const { app, doc, message } = await setup();
  app.ontoolresult({});
  assert.match(message(), /표시할 상태가 없/);
  app.ontoolresult(result({ status: 'made-up' }));
  assert.match(message(), /응답 형식/);
  app.ontoolresult({ isError: true, content: [{ text: 'secret-key' }] });
  assert.match(message(), /실패/);
  assert.equal(doc.body.textContent.includes('secret-key'), false);
  assert.equal(doc.getElementById('details').hidden, true);
});

test('서버 데이터는 HTML이 아니라 텍스트로 표시한다', async () => {
  const { app, doc } = await setup();
  const payload = '<img src=x onerror="alert(1)">';
  app.ontoolresult(result({ limitations: [payload], version_id: payload }));
  assert.equal(doc.querySelector('img'), null);
  assert.equal(doc.getElementById('version').textContent, payload);
});

test('새로고침은 기존 작업의 읽기 전용 도구만 한 번 호출한다', async () => {
  let finish;
  const requests = [];
  const { app, doc, message } = await setup({ callServerTool: (args) => {
    requests.push(args);
    return new Promise((resolve) => { finish = resolve; });
  } });
  app.ontoolresult(result());
  doc.getElementById('refresh').click();
  doc.getElementById('refresh').click();
  assert.match(message(), /새로 확인/);
  assert.deepEqual(requests, [{ name: 'get_paper_status', arguments: { job_id: 'job-1' } }]);
  finish(result({ status: 'processing', stage: 'translate' }));
  await tick();
  assert.match(message(), /처리하고/);
  assert.equal(doc.getElementById('refresh').disabled, false);
});

test('오래된 새로고침 결과가 새 호스트 결과를 덮어쓰지 않는다', async () => {
  let finish;
  const { app, doc } = await setup({ callServerTool: () => new Promise((resolve) => { finish = resolve; }) });
  app.ontoolresult(result());
  doc.getElementById('refresh').click();
  app.ontoolresult(result({ job_id: 'job-2' }));
  finish(result({ job_id: 'job-1' }));
  await tick();
  assert.equal(doc.getElementById('job').textContent, 'job-2');
});

test('연결 실패·조회 취소를 표시한다', async () => {
  const { app, doc, message } = await setup({ connect: async () => { throw new Error('secret'); } });
  assert.match(message(), /연결하지 못/);
  assert.equal(doc.getElementById('refresh').disabled, true);
  app.ontoolcancelled({});
  assert.match(message(), /취소/);
});

test('응답 없는 새로고침은 대기 안내로 끝나고 자동 재시도하지 않는다', async () => {
  const { app, doc, message } = await setup({ callServerTool: () => new Promise(() => {}) }, 10);
  app.ontoolresult(result());
  doc.getElementById('refresh').click();
  await new Promise((resolve) => setTimeout(resolve, 25));
  assert.match(message(), /확인하지 못/);
  assert.equal(doc.getElementById('refresh').disabled, false);
});

test('첫 결과 이후 새 입력의 응답이 없으면 대기를 끝내고 늦은 결과도 표시한다', async () => {
  const { app, doc, calls, message } = await setup({}, 20);
  app.ontoolresult(result());
  app.ontoolinput({ arguments: { job_id: 'job-2' } });
  assert.equal(doc.querySelector('main').getAttribute('aria-busy'), 'true');
  assert.equal(doc.getElementById('refresh').disabled, true);
  await new Promise((resolve) => setTimeout(resolve, 45));
  assert.match(message(), /지연/);
  assert.equal(doc.querySelector('main').getAttribute('aria-busy'), 'false');
  assert.equal(doc.getElementById('refresh').disabled, false);
  assert.deepEqual(calls, []);
  app.ontoolresult(result({ job_id: 'job-2' }));
  assert.match(message(), /완료/);
  assert.equal(doc.getElementById('job').textContent, 'job-2');
});

test('연속 입력은 마지막 입력 시점부터 대기 시간을 다시 계산한다', async () => {
  const { app, doc, message } = await setup({}, 500);
  app.ontoolresult(result());
  app.ontoolinput({ arguments: { job_id: 'job-2' } });
  await new Promise((resolve) => setTimeout(resolve, 150));
  app.ontoolinput({ arguments: { job_id: 'job-3' } });
  await new Promise((resolve) => setTimeout(resolve, 400));
  assert.match(message(), /불러오고/);
  assert.equal(doc.getElementById('refresh').disabled, true);
  await new Promise((resolve) => setTimeout(resolve, 150));
  assert.match(message(), /지연/);
  assert.equal(doc.getElementById('refresh').disabled, false);
});

for (const outcome of ['result', 'cancel', 'error']) {
  test(`새 입력 후 ${outcome} 알림이 오면 대기 타이머가 화면을 덮어쓰지 않는다`, async () => {
    const { app, message } = await setup({}, 20);
    app.ontoolresult(result());
    app.ontoolinput({ arguments: { job_id: 'job-2' } });
    if (outcome === 'cancel') app.ontoolcancelled({});
    else app.ontoolresult(outcome === 'error' ? { isError: true } : result({ job_id: 'job-2' }));
    const displayed = message();
    await new Promise((resolve) => setTimeout(resolve, 45));
    assert.equal(message(), displayed);
    assert.doesNotMatch(message(), /지연/);
  });
}
