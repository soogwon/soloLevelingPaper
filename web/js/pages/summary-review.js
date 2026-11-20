import { ApiError, request } from '../api/client.js';
import { html, setHTML } from '../lib/html.js';

export default async function summaryReview({ view, params }) {
  document.title = '요약 전송 검토 · Solo Leveling';
  const key = params.requestKey;
  if (!/^[0-9a-f-]{36}$/i.test(key)) { view.textContent = '요약 요청 키를 확인해 주세요.'; return; }
  const path = `/remote-summaries/reviews/${encodeURIComponent(key)}`;
  let closed = false, busy = false, value = null, saved = null, generation = 0;
  const events = ['auth:expired', 'auth:logout', 'hashchange', 'pagehide'];
  function close() {
    closed = true; generation++; value = null; saved = null;
    view.textContent = '';
    events.forEach(event => window.removeEventListener(event, close));
  }
  events.forEach(event => window.addEventListener(event, close));
  function validTransfer(v) {
    return v?.notice_version === 'remote-summary-v1' && v.automatic_generation === false
      && v.policy?.max_calls === 1 && Number.isSafeInteger(v.policy.max_input_tokens)
      && v.policy.max_input_tokens > 0 && Number.isSafeInteger(v.policy.max_output_tokens)
      && v.policy.max_output_tokens > 0 && Number.isSafeInteger(v.source_count) && v.source_count > 0
      && ['paper', 'section'].includes(v.scope) && typeof v.model === 'string' && !!v.model;
  }
  function draw(message = '') {
    if (closed || !view.isConnected) return;
    setHTML(view, html`<h1>요약 전송 검토</h1>
      <p>요청 키: ${key}</p><p role="status">${message}</p>
      ${value ? html`<section class="card panel">
        <p>실행 상태: ${value.state || '미접수'} · 실행 ID: ${value.execution_id || '없음'}</p><h2>요약 전송 범위</h2>
        <p>논문 판본: ${value.server_version_id} · 게시: ${value.publication_id}</p>
        <p>범위: ${value.scope === 'paper' ? '논문 전체' : '선택 섹션'} · 섹션: ${value.section_id || '전체'} · 근거 ${value.source_count}개
          ${value.total_scope_chunks > value.source_count ? `(전체 ${value.total_scope_chunks}개 중 선택)` : ''}</p>
        <p>제공자: ${value.provider} · 생성 모델: ${value.model}</p>
        <p>최대 ${value.policy?.max_calls}회 · 입력 ${value.policy?.max_input_tokens}토큰 · 출력 ${value.policy?.max_output_tokens}토큰</p>
        <p>서비스 예산을 사용하며 비용이 발생합니다. 자동 생성·자동 재전송은 하지 않습니다.</p>
        ${validTransfer(value) ? '' : html`<p>전송 범위와 상한을 확인할 수 없습니다. 다시 조회해 주세요.</p>`}
        <p>동의·접수 가능 기한: ${value.expires_at}</p>
        ${['storage', 'ai_provider'].map(p => html`<fieldset>
          <legend>${p === 'storage' ? '요약·결과 저장' : 'AI 제공자 전송'}</legend>
          <p>${p === 'storage' ? value.storage_notice : value.ai_notice}</p><p>상태: ${value[p]}</p>
          <label><input type="checkbox" data-check="${p}" ${busy || !value.active || !validTransfer(value) ? 'disabled' : ''}> 위 내용을 확인하고 동의합니다.</label>
          <button type="button" data-grant="${p}" disabled>동의 저장</button>
          <button type="button" data-revoke="${p}" ${busy || value[p] !== 'current' ? 'disabled' : ''}>동의 철회</button>
        </fieldset>`)}
        <button type="button" data-submit ${busy || !value.active || !validTransfer(value) || value.storage !== 'current' || value.ai_provider !== 'current' ? 'disabled' : ''}>동의한 요약 접수</button>
        <button type="button" data-result ${busy || !value.execution_id ? 'disabled' : ''}>기존 요약 결과 조회</button>
        <button type="button" data-cancel ${busy || !['queued', 'running'].includes(value.state) ? 'disabled' : ''}>요약 취소</button>
        <p>이미 전송한 호출의 비용은 취소되지 않습니다.</p>
        ${saved ? html`<section><h2>저장된 요약</h2><p>${saved.generated_at}</p>
          ${saved.items.map(item => html`<p>${item.kind}: ${item.text}</p><p>근거: ${item.evidence_ids.join(', ')}</p>`)}</section>` : ''}
        <p>철회한 동의는 이 요청에서 다시 활성화하지 않습니다. 오류나 응답 유실 시 새 키를 만들지 말고 기존 상태를 확인하세요.</p>
      </section>` : ''}
      <button type="button" data-refresh ${busy ? 'disabled' : ''}>같은 요청 다시 조회</button>`);
    view.querySelector('[data-refresh]').onclick = () => run('GET', '');
    view.querySelectorAll('[data-check]').forEach(el => { el.onchange = () => {
      view.querySelector(`[data-grant="${el.dataset.check}"]`).disabled = !el.checked || busy || !value.active || !validTransfer(value);
    }; });
    view.querySelectorAll('[data-grant]').forEach(el => { el.onclick = () => run('POST', `/consents/${el.dataset.grant}`, { notice_version: value.notice_version }); });
    view.querySelectorAll('[data-revoke]').forEach(el => { el.onclick = () => run('POST', `/consents/${el.dataset.revoke}/revoke`); });
    const submit = view.querySelector('[data-submit]');
    if (submit) submit.onclick = () => run('POST', '/submit');
    const result = view.querySelector('[data-result]');
    if (result) result.onclick = () => run('GET', '/result');
    const cancel = view.querySelector('[data-cancel]');
    if (cancel) cancel.onclick = () => {
      if (['queued', 'running'].includes(value?.state)) return run('POST', '/cancel');
    };
  }
  async function run(method, suffix, body) {
    if (closed || busy) return;
    if (method === 'POST' && !suffix.endsWith('/revoke') && suffix !== '/cancel' && (!value?.active || !validTransfer(value))) return;
    const seq = ++generation; busy = true; draw('확인 중입니다.');
    try {
      const result = await request(method, path + suffix, { body });
      if (closed || seq !== generation || !view.isConnected) return;
      busy = false;
      if (suffix === '/submit' || suffix === '/cancel') {
        value = { ...value, state: result.state, execution_id: result.execution_id };
        draw(`기존 요청 접수 상태: ${result.state} · 실행 ID: ${result.execution_id}`);
      }
      else if (suffix === '/result') {
        saved = result.result;
        value = { ...value, state: result.state, execution_id: result.execution_id };
        draw(saved ? '기존 결과를 조회했습니다. 새 생성은 하지 않았습니다.' : '저장 완료된 요약이 없습니다.');
      }
      else { value = result; draw(); }
    } catch (error) {
      if (closed || seq !== generation || !view.isConnected) return;
      busy = false;
      const reasons = {
        summary_consent_required: '요약 저장과 AI 전송 동의를 웹에서 다시 확인해 주세요.',
        summary_intake_closed: '현재 새 요약 접수가 중지돼 있어요.',
        summary_budget_unavailable: '현재 계정 또는 서비스의 예산으로 새 요약을 접수할 수 없어요.',
        summary_review_required: '요약 검토 범위와 유효 기간을 다시 확인해 주세요.',
      };
      draw(error instanceof ApiError && Object.hasOwn(reasons, error.code) ? reasons[error.code]
        : '확인하지 못했습니다. 같은 요청을 다시 조회하세요. 자동 재제출하지 않습니다.');
    }
  }
  await run('GET', '');
}
