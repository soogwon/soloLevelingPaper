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

test('읽기 전 논문의 시작 링크와 처음 펼칠 때만 실행되는 처리 작업 조회', async t => {
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
  assert.ok(view.innerHTML.includes(`#/papers/${paperId}/start-learning?version=${version}`));
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
