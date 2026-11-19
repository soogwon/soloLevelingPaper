import { request } from '../api/client.js';
import { html, setHTML } from '../lib/html.js';

export default async function quizConsent({ view, params }) {
  document.title = '퀴즈 답안 저장 동의 · Solo Leveling';
  const context = params.contextId, quiz = params.quizId;
  if (![context, quiz].every(id => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id))) {
    view.textContent = '학습 맥락 ID를 확인해 주세요.'; return;
  }
  const path = `/remote-quizzes/${encodeURIComponent(context)}/${encodeURIComponent(quiz)}/consent`;
  let closed = false, busy = false, value = null, checked = false;
  const events = ['auth:expired', 'auth:logout', 'hashchange', 'pagehide'];
  function close() {
    closed = true; value = null; checked = false; view.textContent = '';
    events.forEach(event => window.removeEventListener(event, close));
  }
  events.forEach(event => window.addEventListener(event, close));
  function valid(v) {
    return v?.context_id === context && v.quiz_id === quiz && v.notice_version === 'remote-quiz-attempt-v1'
      && /^[0-9a-f]{64}$/.test(v.quiz_revision) && ['current', 'missing'].includes(v.storage)
      && typeof v.notice === 'string' && !!v.notice;
  }
  function draw(message = '') {
    if (closed || !view.isConnected) return;
    setHTML(view, html`<h1>퀴즈 답안 저장 동의</h1>
      <p>학습 맥락: ${context} · 퀴즈: ${quiz}</p><p role="status">${message}</p>
      <p>제출한 답안·점수·오답 근거를 이 학습 맥락의 고정 퀴즈에 저장합니다. AI 생성·전송 동의는 포함하지 않습니다.</p>
      <p>동의 철회는 새 제출 저장을 막습니다. 기존 제출 기록은 삭제되지 않으며 같은 요청 결과는 다시 조회할 수 있습니다.</p>
      ${valid(value) ? html`<section class="card panel"><p>${value.notice}</p>
        <p>저장 동의: ${value.storage === 'current' ? '동의됨' : '없음'}</p>
        <label><input type="checkbox" data-check ${busy || value.storage === 'current' ? 'disabled' : ''}> 저장 범위를 확인하고 동의합니다.</label>
        <button type="button" data-grant disabled>저장 동의</button>
        <button type="button" data-revoke ${busy || value.storage !== 'current' ? 'disabled' : ''}>동의 철회</button></section>` : ''}
      <button type="button" data-refresh ${busy ? 'disabled' : ''}>현재 상태 다시 조회</button>`);
    view.querySelector('[data-refresh]').onclick = () => run('GET');
    const checkbox = view.querySelector('[data-check]');
    if (checkbox) checkbox.onchange = () => {
      checked = !!checkbox.checked;
      view.querySelector('[data-grant]').disabled = !checked || busy || !valid(value) || value.storage !== 'missing';
    };
    const grant = view.querySelector('[data-grant]');
    if (grant) grant.onclick = () => run('POST');
    const revoke = view.querySelector('[data-revoke]');
    if (revoke) revoke.onclick = () => run('POST', '/revoke');
  }
  async function run(method, suffix = '') {
    if (closed || busy) return;
    if (method === 'POST' && (!valid(value) || (suffix ? value.storage !== 'current' : !checked || value.storage !== 'missing'))) return;
    const body = method === 'POST' && !suffix ? { notice_version: value.notice_version, quiz_revision: value.quiz_revision } : undefined;
    busy = true; checked = false; draw('확인 중입니다.');
    try {
      const result = await request(method, path + suffix, { body });
      if (closed || !view.isConnected) return;
      if (!valid(result)) throw Error('응답 불일치');
      value = result; busy = false; draw();
    } catch {
      if (closed || !view.isConnected) return;
      value = null; busy = false;
      draw('상태를 확인하지 못했습니다. 다시 조회해 주세요. 자동 동의나 재전송은 하지 않습니다.');
    }
  }
  await run('GET');
}
