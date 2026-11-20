import { remoteAnnotationKind } from '../lib/annotation.js';
import { request } from '../api/client.js';
import { html, setHTML } from '../lib/html.js';

// 인증 세션이 바뀌면 진행 중 응답과 기존 기록을 즉시 폐기한다.
export default async function remoteLearning({ view, params }) {
  const context = params.contextId;
  if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(context)) {
    view.textContent = '학습 맥락을 확인해 주세요.'; return;
  }
  const base = `/remote-annotation-products/contexts/${context}`;
  let closed = false, busy = false, generation = 0, learning = null, consent = null, items = [], cursor = null, pendingProgress = null;
  const events = ['auth:expired', 'auth:logout', 'hashchange', 'pagehide'];
  function close() {
    closed = true; generation++; items = []; consent = null; learning = null; pendingProgress = null; view.textContent = '';
    events.forEach(event => window.removeEventListener(event, close));
  }
  events.forEach(event => window.addEventListener(event, close));
  function accessDenied(error, missing = false) {
    if (![401, 403, ...(missing ? [404] : [])].includes(error.status)) return false;
    close(); view.textContent = '기록에 접근할 수 없습니다. 로그인과 학습 맥락을 확인한 뒤 화면을 다시 열어 주세요.';
    return true;
  }
  function current(seq) { return !closed && view.isConnected && generation === seq; }
  function draw(message = '') {
    if (closed || !view.isConnected) return;
    setHTML(view, html`<h1>원격 학습 기록</h1><p role="status">${message}</p>
      <p>학습 맥락: ${context}</p>
      ${learning ? html`<p>기록 ID: ${learning.learning_record_id} · ${learning.status}</p>
        <p>근거 판본: ${learning.server_version_id} · 게시 ID: ${learning.publication_id}</p>
        <section class="card panel"><h2>읽기 상태와 최근 위치</h2>
          <p>처리 완료와 학습 완료는 별개입니다. 완료·재열기는 메모를 삭제하지 않습니다.</p>
          <label>읽기 상태 <select data-progress-status ${busy || pendingProgress ? 'disabled' : ''}>
            <option value="reading" ${learning.status === 'reading' ? 'selected' : ''}>읽는 중</option>
            <option value="completed" ${learning.status === 'completed' ? 'selected' : ''}>학습 완료</option>
          </select></label>
          <label>고정 PDF 판본의 페이지 <input data-progress-page type="number" min="1" step="1"
            value="${learning.last_pdf_page ?? ''}" ${busy || pendingProgress ? 'disabled' : ''}></label>
          <p>빈 페이지 입력은 최근 위치를 지웁니다. 논문에 인쇄된 페이지 번호와 다를 수 있습니다.</p>
          <button data-progress-save ${busy || pendingProgress ? 'disabled' : ''}>읽기 상태·위치 저장</button>
          ${pendingProgress ? html`<p>이전 저장의 결과를 확인하지 못했습니다. 같은 입력으로 확인한 뒤 새 변경을 저장하세요.</p>
            <button data-progress-retry ${busy ? 'disabled' : ''}>같은 요청으로 결과 확인</button>` : ''}
        </section>`
      : html`<button data-start ${busy ? 'disabled' : ''}>이 맥락에서 학습 시작</button>`}
      ${consent ? html`<section class="card panel"><h2>메모·하이라이트 서버 저장</h2><p>${consent.notice}</p>
        <p>${consent.granted ? '동의됨' : '동의되지 않음'}</p>
        <label><input type="checkbox" data-check ${busy ? 'disabled' : ''}> 안내를 확인하고 이 학습 맥락의 저장에 동의합니다.</label>
        <button data-grant disabled>동의 저장</button>
        <button data-revoke ${busy || !consent.granted ? 'disabled' : ''}>동의 철회</button></section>` : ''}
      <h2>저장된 메모·하이라이트</h2>
      ${items.map(item => html`<section class="card panel"><p>${remoteAnnotationKind(item)} · revision ${item.revision}</p>
        ${item.segments.map(s => html`<blockquote>${s.quote_snapshot}</blockquote>`)}
        ${item.body ? html`<p>${item.kind === 'highlight' ? '메모 · ' : ''}${item.body}</p>` : ''}
        <textarea data-body="${item.annotation_id}" maxlength="5000" ${busy || !consent?.granted ? 'disabled' : ''}>${item.body}</textarea>
        <button data-update="${item.annotation_id}" ${busy || !consent?.granted ? 'disabled' : ''}>본문 수정</button>
        <button data-delete="${item.annotation_id}" ${busy ? 'disabled' : ''}>기록 삭제</button></section>`)}
      <p>새 메모·하이라이트 작성은 같은 맥락의 원격 MCP 도구를 사용합니다. 충돌·응답 유실 시 먼저 다시 조회하세요.</p>
      <button data-refresh ${busy ? 'disabled' : ''}>다시 조회</button>
      ${cursor ? html`<button data-more ${busy ? 'disabled' : ''}>더 보기</button>` : ''}`);
    const bind = (selector, fn) => { const el = view.querySelector(selector); if (el) el.onclick = fn; };
    bind('[data-refresh]', () => refresh(false));
    bind('[data-more]', () => refresh(true));
    bind('[data-progress-save]', () => saveProgress(false));
    bind('[data-progress-retry]', () => saveProgress(true));
    bind('[data-start]', () => mutate('POST', '/start'));
    const check = view.querySelector('[data-check]');
    if (check) check.onchange = () => { view.querySelector('[data-grant]').disabled = busy || !check.checked; };
    bind('[data-grant]', () => {
      if (view.querySelector('[data-check]')?.checked && consent)
        return mutate('POST', '/consent', { notice_version: consent.notice_version });
    });
    bind('[data-revoke]', () => consent?.granted && mutate('POST', '/consent/revoke', { consent_id: consent.consent_id }));
    view.querySelectorAll('[data-update]').forEach(el => { el.onclick = () => {
      const item = items.find(i => i.annotation_id === el.dataset.update);
      if (item && consent?.granted) return mutate('PATCH', `/annotations/${item.annotation_id}`,
        { body: view.querySelector(`[data-body="${item.annotation_id}"]`).value, step: item.step, revision: item.revision });
    }; });
    view.querySelectorAll('[data-delete]').forEach(el => { el.onclick = () => {
      const item = items.find(i => i.annotation_id === el.dataset.delete);
      if (item && window.confirm('이 기록을 삭제할까요?'))
        return mutate('DELETE', `/annotations/${item.annotation_id}`, { revision: item.revision });
    }; });
  }
  async function refresh(more) {
    if (closed || busy) return;
    busy = true; const seq = ++generation; draw('조회 중입니다.');
    try {
      const record = await request('GET', base + '/learning');
      if (!current(seq)) return;
      if (record.context_id !== context) throw Error('scope');
      const permission = await request('GET', base + '/consent');
      if (!current(seq)) return;
      const page = await request('GET', base + '/annotations' + (more && cursor ? `?after=${encodeURIComponent(cursor)}` : ''));
      if (!current(seq)) return;
      if (!Array.isArray(page.items) || page.items.some(i => i.context_id !== context
          || !/^[0-9a-f-]{36}$/i.test(i.annotation_id) || !Number.isSafeInteger(i.revision) || !Array.isArray(i.segments))) throw Error('scope');
      learning = record; consent = permission;
      items = more ? [...new Map([...items, ...page.items].map(i => [i.annotation_id, i])).values()] : page.items;
      cursor = page.next_cursor; busy = false; draw();
    } catch (error) {
      if (!current(seq)) return;
      if (accessDenied(error, Boolean(learning))) return;
      busy = false;
      // 실패 응답을 학습 미시작으로 단정하지 않고 사용자가 명시적으로 시작하도록 안내한다.
      draw(error.status === 404 ? '기록이 없거나 접근할 수 없습니다. 학습 맥락을 확인해 주세요.' : '조회 실패. 저장·수정을 재전송하기 전에 다시 조회해 주세요.');
    }
  }
  async function saveProgress(retry) {
    if (closed || busy || !learning || (!retry && pendingProgress)) return;
    if (!retry) {
      const raw = view.querySelector('[data-progress-page]').value.trim();
      const page = raw === '' ? null : Number(raw);
      const status = view.querySelector('[data-progress-status]').value;
      if ((page !== null && (!Number.isSafeInteger(page) || page < 1))
          || !['reading', 'completed'].includes(status) || !Number.isSafeInteger(learning.revision)) {
        draw('읽기 상태와 양의 정수 페이지를 확인해 주세요.'); return;
      }
      pendingProgress = { request_key: crypto.randomUUID(), status, pdf_page: page, expected_revision: learning.revision };
    }
    if (!pendingProgress) return;
    busy = true; const seq = ++generation; draw('읽기 상태를 저장합니다.');
    try {
      const result = await request('POST', base + '/progress', { body: pendingProgress });
      if (!current(seq)) return;
      if (result?.state?.context_id !== context || !Number.isSafeInteger(result.state.revision)) throw Error('scope');
      pendingProgress = null; learning = result.state; busy = false; await refresh(false);
    } catch (error) {
      if (!current(seq)) return;
      if (accessDenied(error, true)) return;
      busy = false;
      if (error.status === 409 || error.status === 422) {
        pendingProgress = null; await refresh(false);
        draw('변경 충돌 또는 잘못된 입력입니다. 최신 상태를 확인하고 다시 입력해 주세요.');
      } else draw('저장 결과를 확인하지 못했습니다. 같은 요청으로 결과 확인을 선택해 주세요.');
    }
  }
  async function mutate(method, suffix, body) {
    if (closed || busy) return;
    busy = true; const seq = ++generation; draw('처리 중입니다.');
    try {
      await request(method, base + suffix, { body });
      if (!current(seq)) return;
      busy = false; await refresh(false);
    } catch (error) {
      if (!current(seq)) return;
      if (accessDenied(error, true)) return;
      busy = false; draw('완료를 확인하지 못했습니다. 자동 재전송하지 않으며 먼저 다시 조회해 주세요.');
    }
  }
  await refresh(false);
}
