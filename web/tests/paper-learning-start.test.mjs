import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = Object.assign(new EventTarget(), {
  location: {origin:'https://example.test', search:''},
});
globalThis.document = {cookie:''};

const { default: paperDetailPage } = await import('../js/pages/paper-detail.js');
const { matchRoute } = await import('../js/router.js');
const paperId = '00000000-0000-0000-0000-000000000001';
const version = '00000000-0000-0000-0000-000000000002';
const paper = {
  paper_id:paperId, selected_version_id:version, title:'합성 논문',
  reading_status:'not_started', file_access:{available:true,reason:'file_ready'},
};
const response = value => ({ok:true, status:200, json:async () => value});
function node() {
  return Object.assign(new EventTarget(), {isConnected:true, innerHTML:'', textContent:''});
}

test('읽기 전 논문의 시작 버튼과 처음 펼칠 때만 실행되는 처리 작업 조회', async t => {
  const calls = [], view = node(), jobs = node(), body = node();
  jobs.open = false;
  jobs.querySelector = () => body;
  body.querySelector = () => ({});
  view.querySelector = selector => selector === '.processing-jobs' ? jobs : null;
  t.mock.method(globalThis, 'fetch', async url => {
    calls.push(String(url));
    return response(String(url).includes('processing-jobs') ? {status:'ok',jobs:[]} : paper);
  });
  await paperDetailPage({view, params:{paperId}, query:{version}});
  // 시작 버튼은 별도 화면으로 이동하지 않고 첫 요청 문장 창을 연다.
  assert.match(view.innerHTML, /data-action="remote-start"/);
  assert.doesNotMatch(view.innerHTML, /href="#\/papers\/[^"]*\/start-learning/);
  assert.doesNotMatch(view.innerHTML, /Claude에서 이어서 학습|remote-first-learning/);
  assert.match(view.innerHTML, /<details class="card panel processing-jobs">/);
  jobs.dispatchEvent(new Event('toggle'));
  assert.equal(calls.length, 1);
  jobs.open = true;
  jobs.dispatchEvent(new Event('toggle'));
  jobs.dispatchEvent(new Event('toggle'));
  assert.equal(calls.filter(url => url.includes('processing-jobs')).length, 1);
  await new Promise(resolve => setImmediate(resolve));
  window.dispatchEvent(new Event('pagehide'));
});

test('별도 학습 시작 경로와 논문 상세 복귀 링크가 같은 판본을 사용한다', async t => {
  const {route,params} = matchRoute(`/papers/${paperId}/start-learning`);
  const {default:page} = await route.load();
  const view = node(), panel = node();
  view.querySelector = () => panel;
  panel.querySelector = selector => selector === '[data-check]' ? {} : null;
  const calls = [];
  t.mock.method(globalThis, 'fetch', async url => {
    calls.push(String(url));
    return response(calls.length === 1 ? paper : {
      paper_id:paperId, version_id:version, intent_id:paperId, active_publication_id:version,
    });
  });
  await page({view,params,query:{version}});
  assert.ok(view.innerHTML.includes(`#/papers/${paperId}?version=${version}`));
  assert.ok(calls[0].includes(`version_id=${version}`));
  assert.ok(calls[1].endsWith(`/by-paper/${paperId}/${version}`));
  assert.match(panel.innerHTML, /Claude 요청 복사/);
  window.dispatchEvent(new Event('pagehide'));
});

test('원격 학습 중인 논문은 로컬 기록 없이도 원격 MCP 요청으로 이어서 학습할 수 있다', async t => {
  const view = node();
  view.querySelector = () => null;
  const remotePaper = {
    ...paper, reading_status:'reading', reading_status_source:'remote_learning',
    reading_basis_version_id:version,
    resume:{available:false, reason:'learning_record_missing', message:'저장된 학습 기록이 없어요.'},
  };
  t.mock.method(globalThis, 'fetch', async () => response(remotePaper));
  await paperDetailPage({view, params:{paperId}, query:{version}});
  assert.match(view.innerHTML, /data-action="remote-resume"/);
  assert.doesNotMatch(view.innerHTML, /data-action="remote-resume"[^>]*disabled/);
  assert.doesNotMatch(view.innerHTML, /저장된 학습 기록이 없어요/);
  const { remoteResumePrompt } = await import('../js/pages/paper-detail.js');
  const prompt = remoteResumePrompt(paperId, version);
  assert.ok(prompt.includes(paperId) && prompt.includes(version));
  assert.match(prompt, /open_remote_paper/);
  window.dispatchEvent(new Event('pagehide'));
});

test('원격 학습 근거가 없으면 기존 이어하기 버튼은 그대로 비활성이다', async t => {
  const view = node();
  view.querySelector = () => null;
  const localPaper = {
    ...paper, reading_status:'reading', reading_status_source:'explicit',
    resume:{available:false, reason:'learning_record_missing', message:'저장된 학습 기록이 없어요.'},
  };
  t.mock.method(globalThis, 'fetch', async () => response(localPaper));
  await paperDetailPage({view, params:{paperId}, query:{version}});
  assert.match(view.innerHTML, /data-action="resume"[^>]*disabled/);
  window.dispatchEvent(new Event('pagehide'));
});
