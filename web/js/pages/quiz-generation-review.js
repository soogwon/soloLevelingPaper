import { request } from '../api/client.js';
import { html, setHTML } from '../lib/html.js';

export default async function quizGenerationReview({ view, params }) {
  document.title = '퀴즈 생성 전송 검토 · Solo Leveling';
  const key = params.requestKey;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(key)) {
    view.textContent = '퀴즈 생성 요청 키를 확인해 주세요.'; return;
  }
  const path = `/remote-quiz-generation/reviews/${encodeURIComponent(key)}`;
  let closed = false, busy = false, value = null, generation = 0, deadline = 0, timer;
  const checked = new Set();
  const events = ['auth:expired', 'auth:logout', 'hashchange', 'pagehide'];
  function close() {
    closed = true; generation++; value = null; checked.clear(); clearTimeout(timer);
    view.textContent = '';
    events.forEach(event => window.removeEventListener(event, close));
  }
  events.forEach(event => window.addEventListener(event, close));
  function validTransfer(v) {
    return v?.request_key === key && v.notice_version === 'remote-quiz-generation-v1'
      && /^remote-quiz-generation-v1:[a-f0-9]{64}$/.test(v.input_hash)
      && v.automatic_generation === false && v.policy?.max_calls === 1 && v.max_calls === 1
      && ['preview', 'structure', 'deep_read', 'review'].includes(v.step)
      && ['multiple_choice', 'short_answer'].includes(v.question_type)
      && Number.isSafeInteger(v.question_count) && v.question_count >= 1 && v.question_count <= 20
      && Number.isSafeInteger(v.source_count) && v.source_count >= 1 && v.source_count <= 20
      && ['max_input_tokens', 'max_output_tokens'].every(k => Number.isSafeInteger(v.policy[k])
        && v.policy[k] > 0 && v.policy[k] <= 1000000 && v[k] === v.policy[k])
      && ['provider', 'model', 'storage_notice', 'ai_notice', 'server_version_id', 'publication_id']
        .every(k => typeof v[k] === 'string' && !!v[k].trim());
  }
  function active() { return value?.active === true && validTransfer(value) && performance.now() < deadline; }
  function canGrant(purpose) { return active() && !value.execution_id && value[purpose] === 'missing'; }
  function canSubmit() { return active() && !value.execution_id && value.storage === 'current' && value.ai_provider === 'current'; }
  function setValue(result) {
    value = result; checked.clear(); clearTimeout(timer);
    // 서버 기준 남은 시간으로 표시한다. 최종 접수 허용 여부는 서버가 다시 판단한다.
    const remaining = Date.parse(result?.expires_at) - Date.parse(result?.server_now);
    deadline = Number.isFinite(remaining) ? performance.now() + Math.max(0, remaining) : 0;
    if (active()) timer = setTimeout(() => { checked.clear(); draw('검토 기한이 지났습니다. 기존 상태만 조회할 수 있습니다.'); }, Math.min(remaining + 1, 86400001));
  }
  function draw(message = '') {
    if (closed || !view.isConnected) return;
    setHTML(view, html`<h1>퀴즈 생성 전송 검토</h1><p>요청 키: ${key}</p><p role="status">${message}</p>
      ${value ? html`<section class="card panel">
        <p>실행 상태: ${value.state || '미접수'} · 실행 ID: ${value.execution_id || '없음'}</p>
        <p>논문 판본: ${value.server_version_id} · 게시: ${value.publication_id}</p>
        <p>학습 단계: ${value.step} · 문항 ${value.question_count}개 · 유형: ${value.question_type} · 근거 ${value.source_count}개</p>
        <p>제공자: ${value.provider} · 생성 모델: ${value.model}</p>
        <p>최대 ${value.policy?.max_calls}회 · 입력 ${value.policy?.max_input_tokens}토큰 · 출력 ${value.policy?.max_output_tokens}토큰</p>
        <p>서비스 예산을 사용하며 비용이 발생합니다. 접수 시 예산을 예약합니다. 자동 생성·자동 재전송은 하지 않습니다.</p>
        <p>동의·접수 가능 기한: ${value.expires_at}</p>
        ${!active() ? html`<p>검토 기한이 지났거나 전송 범위가 변경되었습니다. 상태를 다시 조회하세요.</p>` : ''}
        ${['storage', 'ai_provider'].map(p => html`<fieldset>
          <legend>${p === 'storage' ? '퀴즈·정답 저장' : 'AI 제공자 전송'}</legend>
          <p>${p === 'storage' ? value.storage_notice : value.ai_notice}</p><p>동의 상태: ${value[p]}</p>
          <label><input type="checkbox" data-check="${p}" ${busy || !canGrant(p) ? 'disabled' : ''}> 위 내용을 확인하고 동의합니다.</label>
          <button type="button" data-grant="${p}" disabled>동의 저장</button>
          <button type="button" data-revoke="${p}" ${busy || value[p] !== 'current' ? 'disabled' : ''}>동의 철회</button>
        </fieldset>`)}
        <button type="button" data-submit ${busy || !canSubmit() ? 'disabled' : ''}>동의한 퀴즈 생성 접수</button>
        <button type="button" data-cancel ${busy || !['queued', 'running'].includes(value.state) ? 'disabled' : ''}>접수 취소</button>
        <p>답안 제출 저장 동의는 별도입니다. 철회한 동의를 같은 요청에서 다시 켜지 않습니다.</p>
        <p>이미 전송한 호출의 비용은 취소되지 않습니다. 응답 유실 시 새 키를 만들지 말고 기존 상태를 조회하세요.</p>
      </section>` : ''}
      <button type="button" data-refresh ${busy ? 'disabled' : ''}>같은 요청 다시 조회</button>`);
    view.querySelector('[data-refresh]').onclick = () => run('GET', '');
    view.querySelectorAll('[data-check]').forEach(el => { el.onchange = () => {
      el.checked ? checked.add(el.dataset.check) : checked.delete(el.dataset.check);
      view.querySelector(`[data-grant="${el.dataset.check}"]`).disabled = !el.checked || busy || !canGrant(el.dataset.check);
    }; });
    view.querySelectorAll('[data-grant]').forEach(el => { el.onclick = () => {
      const p = el.dataset.grant;
      if (checked.has(p) && canGrant(p)) return run('POST', `/consents/${p}`, { notice_version: value.notice_version, input_hash: value.input_hash });
    }; });
    view.querySelectorAll('[data-revoke]').forEach(el => { el.onclick = () => {
      if (value?.[el.dataset.revoke] === 'current') return run('POST', `/consents/${el.dataset.revoke}/revoke`);
    }; });
    const submit = view.querySelector('[data-submit]');
    if (submit) submit.onclick = () => { if (canSubmit()) return run('POST', '/submit'); };
    const cancel = view.querySelector('[data-cancel]');
    if (cancel) cancel.onclick = () => { if (['queued', 'running'].includes(value?.state)) return run('POST', '/cancel'); };
  }
  async function run(method, suffix, body) {
    if (closed || busy) return;
    const seq = ++generation; busy = true; checked.clear(); draw('확인 중입니다.');
    try {
      const result = await request(method, path + suffix, { body });
      if (closed || seq !== generation || !view.isConnected) return;
      busy = false;
      if (suffix === '/submit' || suffix === '/cancel') {
        value = { ...value, state: result.state, execution_id: result.execution_id };
        draw(`기존 요청 상태: ${result.state} · 실행 ID: ${result.execution_id}`);
      } else { setValue(result); draw(); }
    } catch {
      if (closed || seq !== generation || !view.isConnected) return;
      busy = false; deadline = 0;
      draw('확인하지 못했습니다. 같은 요청을 다시 조회하세요. 자동 재제출하지 않습니다.');
    }
  }
  await run('GET', '');
}
