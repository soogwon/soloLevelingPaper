import { request } from '../api/client.js';
import { html, setHTML } from '../lib/html.js';

export default async function questionReview({ view, params }) {
  document.title = '질문 전송 검토 · Solo Leveling';
  const key = params.requestKey;
  if (!/^[0-9a-f-]{36}$/i.test(key)) { view.textContent = '질문 요청 키를 확인해 주세요.'; return; }
  const path = `/remote-questions/reviews/${encodeURIComponent(key)}`;
  let closed = false, busy = false, value = null, generation = 0;
  const events = ['auth:expired', 'auth:logout', 'hashchange', 'pagehide'];
  function close() {
    closed = true; generation++; value = null;
    view.textContent = '';
    events.forEach(event => window.removeEventListener(event, close));
  }
  events.forEach(event => window.addEventListener(event, close));
  function draw(message = '') {
    if (closed || !view.isConnected) return;
    setHTML(view, html`<h1>질문 전송 검토</h1>
      <p>요청 키: ${key}</p><p role="status">${message}</p>
      ${value ? html`<section class="card panel">
        <p>실행 상태: ${value.state || '미접수'} · 실행 ID: ${value.execution_id || '없음'}</p><h2>질문과 전송 범위</h2><p>${value.question}</p>
        <p>논문 판본: ${value.version_id} · 페이지: ${value.pages.join(', ') || '전체'} · 근거 최대 ${value.top_k}개</p>
        <p>섹션: ${value.sections.join(', ') || '전체'} · 페이지 엄격 제한: ${value.strict_pages ? '예' : '아니요'}</p>
        <p>생성 모델: ${value.models.generation_model} · 의미 검증: ${value.models.semantic_model}</p>
        <p>최대 호출 ${value.policy.max_calls}회 · 입력 ${value.policy.max_input_tokens}토큰 · 출력 ${value.policy.max_output_tokens}토큰</p>
        <p>동의·접수 가능 기한: ${value.expires_at}</p>
        ${['storage', 'ai_provider'].map(p => html`<fieldset>
          <legend>${p === 'storage' ? '질문·결과 저장' : 'OpenAI 전송'}</legend>
          <p>${p === 'storage' ? value.storage_notice : value.ai_notice}</p><p>상태: ${value[p]}</p>
          <label><input type="checkbox" data-check="${p}" ${busy || !value.active ? 'disabled' : ''}> 위 내용을 확인하고 동의합니다.</label>
          <button type="button" data-grant="${p}" disabled>동의 저장</button>
          <button type="button" data-revoke="${p}" ${busy || value[p] !== 'current' ? 'disabled' : ''}>동의 철회</button>
        </fieldset>`)}
        <button type="button" data-submit ${busy || !value.active || value.storage !== 'current' || value.ai_provider !== 'current' ? 'disabled' : ''}>동의한 질문 접수</button>
        <p>철회한 동의는 이 요청에서 다시 활성화하지 않습니다. 오류나 응답 유실 시 새 키를 만들지 말고 기존 상태를 확인하세요.</p>
      </section>` : ''}
      <button type="button" data-refresh ${busy ? 'disabled' : ''}>같은 요청 다시 조회</button>`);
    view.querySelector('[data-refresh]').onclick = () => run('GET', '');
    view.querySelectorAll('[data-check]').forEach(el => { el.onchange = () => {
      view.querySelector(`[data-grant="${el.dataset.check}"]`).disabled = !el.checked || busy || !value.active;
    }; });
    view.querySelectorAll('[data-grant]').forEach(el => { el.onclick = () => run('POST', `/consents/${el.dataset.grant}`, { notice_version: value.notice_version }); });
    view.querySelectorAll('[data-revoke]').forEach(el => { el.onclick = () => run('POST', `/consents/${el.dataset.revoke}/revoke`); });
    const submit = view.querySelector('[data-submit]');
    if (submit) submit.onclick = () => run('POST', '/submit');
  }
  async function run(method, suffix, body) {
    if (closed || busy) return;
    const seq = ++generation; busy = true; draw('확인 중입니다.');
    try {
      const result = await request(method, path + suffix, { body });
      if (closed || seq !== generation || !view.isConnected) return;
      busy = false;
      if (suffix === '/submit') {
        value = { ...value, state: result.state, execution_id: result.execution_id };
        draw(`기존 요청 접수 상태: ${result.state} · 실행 ID: ${result.execution_id}`);
      }
      else { value = result; draw(); }
    } catch {
      if (closed || seq !== generation || !view.isConnected) return;
      busy = false; draw('확인하지 못했습니다. 같은 요청을 다시 조회하세요. 자동 재제출하지 않습니다.');
    }
  }
  await run('GET', '');
}
