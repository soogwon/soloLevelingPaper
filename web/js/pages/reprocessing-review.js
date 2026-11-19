import { request } from '../api/client.js';
import { html, setHTML } from '../lib/html.js';
import { replaceQuery } from '../router.js';

export default async function reprocessingReview({ view, params, query = {} }) {
  const { contextId, operation } = params;
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (!uuid.test(contextId) || !['translation_recovery', 'reparse'].includes(operation)) {
    view.textContent = '복구 대상 주소를 확인해 주세요.'; return;
  }
  const key = query.key || crypto.randomUUID();
  if (!uuid.test(key)) { view.textContent = '요청 키를 확인해 주세요.'; return; }
  if (!query.key) replaceQuery({ key });
  const path = `/reprocessing/reviews/${key}`;
  let value = null, closed = false, busy = false, generation = 0;
  const events = ['auth:logout', 'auth:expired', 'hashchange', 'pagehide'];
  function close() {
    closed = true; generation++; value = null; view.textContent = '';
    events.forEach(event => window.removeEventListener(event, close));
  }
  events.forEach(event => window.addEventListener(event, close));
  function valid() {
    const c = value?.contract;
    return c?.notice_version === 'remote-reprocessing-v1' && c.execution_enabled === false
      && c.token_limit_scope === 'entire_job' && typeof c.max_cost_usd === 'string'
      && Number.isFinite(Number(c.max_cost_usd)) && Number(c.max_cost_usd) > 0
      && ['max_calls', 'max_input_tokens', 'max_output_tokens'].every(k => Number.isSafeInteger(c[k]) && c[k] > 0)
      && value.snapshot?.preview?.context_id === contextId && value.snapshot.preview.operation === operation;
  }
  function draw(message = '') {
    if (closed || !view.isConnected) return;
    const p = value?.snapshot?.preview, c = value?.contract;
    setHTML(view, html`<h1>${operation === 'reparse' ? 'PDF 재파싱' : '번역 복구'} 검토</h1>
      <p role="status">${message}</p><p>요청 키: ${key}</p>
      ${value ? html`<section class="card panel">
        <p>판본 ${p?.version_id} · 게시 ${p?.publication_id}</p>
        <p>대상 ${p?.target_count}${operation === 'reparse' ? '개 PDF 판본' : '개 청크'}</p>
        <p>저장: 새 파싱·번역·검색 색인과 작업 이력. 기존 학습 맥락과 근거는 자동 교체하지 않습니다.</p>
        <p>전송 예정: ${operation === 'reparse' ? '저장된 PDF를 서버에서 다시 파싱하고, 번역 선택 시 추출 원문과 지시를 AI 제공자에 전송' : '복구 대상 원문과 번역 지시를 AI 제공자에 전송'}</p>
        <p>제공자 ${c?.provider} · 모델 ${c?.model}</p>
        <p>작업 전체 최대 ${c?.max_calls}회 · 입력 ${c?.max_input_tokens}토큰 · 출력 ${c?.max_output_tokens}토큰</p>
        <p>설정 단가 기준 상한 $${c?.max_cost_usd} USD. 사용자·서비스 예산을 함께 예약합니다. 실제 비용 측정값이 아닙니다.</p>
        <p>접수만으로 실행하지 않습니다. 별도 실행 승인 후 처리하며 재파싱은 원문 추출만 수행합니다. 새 색인은 게시 후 별도로 선택해야 적용됩니다. 기존 학습 맥락은 유지됩니다. 취소해도 이미 전송한 요청의 비용은 반환하지 않습니다.</p>
        <p>상태: ${({queued:'접수됨·실행 승인 대기',running:'처리 중',staged:'결과 저장됨·새 색인 게시 대기',published:'새 색인 게시됨·선택 대기',failed:'처리 중단',cancelled:'취소됨'})[value.state] || value.state} · 작업 ID: ${value.job_id || '없음'}</p>
        ${value.publication ? html`<p>새 게시 ${value.publication.publication_id} · 색인 ${value.publication.embedding_set_id}</p>
          <p>새 색인을 선택하면 새 학습 맥락을 만듭니다. 이전 메모·질문·근거는 이전 맥락에 남습니다.</p>
          <button data-select ${busy || !value.publication_ready || value.storage !== 'current' || value.publication.selected_context_id ? 'disabled' : ''}>새 색인 선택·새 학습 시작</button>
          ${value.publication.selected_context_id ? html`<a href="#/remote-learning/${encodeURIComponent(value.publication.selected_context_id)}">선택한 학습 맥락 열기</a>` : ''}` : ''}
        <p>검토 만료: ${value.expires_at || '검토 저장 후 확정'}</p>
        ${valid() ? '' : html`<p>계약 또는 대상이 일치하지 않습니다. 변경 작업을 차단했습니다.</p>`}
        ${value.state === 'draft' ? html`<button data-prepare ${busy || !valid() ? 'disabled' : ''}>이 요청 키로 검토 저장</button>` : ''}
        ${['storage', 'ai_provider'].map(purpose => html`<fieldset>
          <legend>${purpose === 'storage' ? '결과 저장 동의' : 'AI 전송 동의'}</legend><p>${value[purpose]}</p>
          <label><input type="checkbox" data-check="${purpose}" ${busy || !value.active || value.state !== 'review' || !valid() ? 'disabled' : ''}> 위 범위와 비용 상한을 확인했습니다.</label>
          <button data-grant="${purpose}" disabled>동의 저장</button>
          <button data-revoke="${purpose}" ${busy || value[purpose] !== 'current' ? 'disabled' : ''}>철회하고 접수 취소</button>
        </fieldset>`)}
        <button data-submit ${busy || !valid() || !value.active || value.state !== 'review' || value.storage !== 'current' || value.ai_provider !== 'current' ? 'disabled' : ''}>동의한 작업 접수</button>
        <button data-cancel ${busy || !['review', 'queued', 'running', 'staged'].includes(value.state) ? 'disabled' : ''}>취소·예약 반환</button>
      </section>` : ''}<button data-refresh ${busy ? 'disabled' : ''}>같은 요청 다시 조회</button>`);
    view.querySelector('[data-refresh]').onclick = () => run('GET', path);
    const prepare = view.querySelector('[data-prepare]');
    if (prepare) prepare.onclick = () => { if (valid() && value.state === 'draft') return run('POST', path, { context_id: contextId, operation }); };
    view.querySelectorAll('[data-check]').forEach(el => { el.onchange = () => {
      view.querySelector(`[data-grant="${el.dataset.check}"]`).disabled = !el.checked || busy || !valid() || !value.active;
    }; });
    view.querySelectorAll('[data-grant]').forEach(el => { el.onclick = () => {
      if (valid() && value.active && value.state === 'review') return run('POST', `${path}/consents/${el.dataset.grant}`, { notice_version: c.notice_version });
    }; });
    view.querySelectorAll('[data-revoke]').forEach(el => { el.onclick = () => run('POST', `${path}/consents/${el.dataset.revoke}/revoke`); });
    const submit = view.querySelector('[data-submit]');
    if (submit) submit.onclick = () => { if (valid() && value.active && value.state === 'review' && value.storage === 'current' && value.ai_provider === 'current') return run('POST', `${path}/submit`); };
    const select = view.querySelector('[data-select]');
    if (select) select.onclick = () => {
      if (value?.publication_ready && value.storage === 'current' && value.publication?.publication_id && !value.publication.selected_context_id)
        return run('POST', `${path}/select`, { publication_id: value.publication.publication_id });
    };
    const cancel = view.querySelector('[data-cancel]');
    if (cancel) cancel.onclick = () => { if (['review', 'queued', 'running', 'staged'].includes(value?.state)) return run('POST', `${path}/cancel`); };
  }
  async function run(method, target, body) {
    if (closed || busy) return;
    const seq = ++generation; busy = true; draw('확인 중');
    try {
      let result;
      try { result = await request(method, target, { body }); }
      catch (error) {
        if (closed || seq !== generation) return;
        // 최초 진입/미저장 요청만 읽기 전용 미리보기로 복원한다. 자동 POST는 없다.
        if (method !== 'GET' || error.status !== 404) throw error;
        result = await request('GET', `/reprocessing/contexts/${contextId}/${operation}`);
      }
      if (closed || seq !== generation || !view.isConnected) return;
      value = result.snapshot ? result : { ...value, ...result, active: false };
      busy = false; draw();
    } catch {
      if (closed || seq !== generation || !view.isConnected) return;
      busy = false; draw('응답을 확인하지 못했습니다. 같은 요청을 조회해 주세요. 자동 재접수하지 않습니다.');
    }
  }
  await run('GET', path);
}
