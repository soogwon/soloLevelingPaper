import { remoteAnnotationKind } from './annotation.js';
import { request } from '../api/client.js';
import { html, setHTML } from './html.js';

// 판본별 원격 맥락과 기록은 로컬 동기화 목록과 별도로 조회한다.
export async function remotePaperNotes(view, paper, version) {
  const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
  if (!view || !uuid(paper) || typeof version !== 'string' || !version.trim() || version.length > 128) return;
  let closed = false, seq = 0, contexts = [], next = null;
  let selected = null, items = [], more = null, busy = false;
  const events = ['auth:logout', 'auth:expired', 'hashchange', 'pagehide'];
  const close = () => {
    closed = true; seq++; contexts = []; items = []; view.textContent = '';
    events.forEach(event => window.removeEventListener(event, close));
  };
  events.forEach(event => window.addEventListener(event, close));
  const current = n => !closed && view.isConnected && seq === n;
  function draw(message = '') {
    if (closed || !view.isConnected) return;
    setHTML(view, html`<h2>원격 메모·하이라이트</h2><p role="status">${message}</p>
      <p>현재 판본의 학습 맥락을 선택해 저장된 기록을 확인하세요.</p>
      ${contexts.map(c => html`<button data-context="${c.context_id}" ${busy ? 'disabled' : ''}
        aria-pressed="${String(c.context_id === selected)}">학습 맥락 ${c.context_id}</button>`)}
      ${next ? html`<button data-context-more ${busy ? 'disabled' : ''}>학습 맥락 더 보기</button>` : ''}
      ${selected ? html`<a href="#/remote-learning/${selected}">이 맥락의 저장 동의·수정·삭제</a>` : ''}
      ${items.map(a => html`<section class="card panel"><p>${remoteAnnotationKind(a)} · revision ${a.revision}</p>
        ${a.segments.map(s => html`<blockquote><span>${s.target === 'translation' ? '번역' : '원문'}</span> ${s.quote_snapshot}</blockquote>`)}
        ${a.body ? html`<p>${a.kind === 'highlight' ? '메모 · ' : ''}${a.body}</p>` : ''}</section>`)}
      ${more ? html`<button data-more ${busy ? 'disabled' : ''}>기록 더 보기</button>` : ''}
      <button data-refresh ${busy ? 'disabled' : ''}>원격 기록 다시 조회</button>`);
    view.querySelectorAll('[data-context]').forEach(el => { el.onclick = () => loadItems(el.dataset.context, false); });
    const bind = (selector, fn) => { const el = view.querySelector(selector); if (el) el.onclick = fn; };
    bind('[data-context-more]', () => loadContexts(true));
    bind('[data-more]', () => loadItems(selected, true));
    bind('[data-refresh]', () => loadContexts(false));
  }
  async function loadContexts(append) {
    if (busy || closed) return;
    busy = true; const n = ++seq; draw('조회 중입니다.');
    try {
      const page = await request('GET', '/remote-annotation-products/contexts', {
        query: { paper_id: paper, version_id: version, after: append ? next : undefined }
      });
      if (!current(n)) return;
      if (!Array.isArray(page.items) || page.items.some(c => !uuid(c.context_id) || c.paper_id !== paper || c.server_version_id !== version)
          || (page.next_cursor != null && !uuid(page.next_cursor))) throw Error('scope');
      contexts = append ? [...new Map([...contexts, ...page.items].map(c => [c.context_id, c])).values()] : page.items;
      next = page.next_cursor; busy = false;
      if (!append) {
        selected = null; items = []; more = null;
        if (contexts.length) { await loadItems(contexts[0].context_id, false); return; }
      }
      draw(contexts.length ? '' : '이 판본에는 시작한 원격 학습 맥락이 없습니다.');
    } catch {
      if (!current(n)) return;
      busy = false; contexts = []; items = []; selected = null; more = null; next = null;
      draw('원격 기록을 조회하지 못했습니다. 기능 활성화와 로그인 상태를 확인한 뒤 다시 조회해 주세요.');
    }
  }
  async function loadItems(context, append) {
    if (busy || closed || !contexts.some(c => c.context_id === context)) return;
    busy = true; const n = ++seq;
    if (!append) { selected = context; items = []; more = null; }
    draw('조회 중입니다.');
    try {
      const page = await request('GET', `/remote-annotation-products/contexts/${context}/annotations`, {
        query: { after: append ? more : undefined }
      });
      if (!current(n)) return;
      if (!Array.isArray(page.items) || page.items.some(a => a.context_id !== context || !uuid(a.annotation_id)
          || !Number.isSafeInteger(a.revision) || a.revision < 1 || !['note', 'highlight'].includes(a.kind)
          || !Array.isArray(a.segments)) || (page.next_cursor != null && !uuid(page.next_cursor))) throw Error('scope');
      items = append ? [...new Map([...items, ...page.items].map(a => [a.annotation_id, a])).values()] : page.items;
      more = page.next_cursor; busy = false;
      draw(items.length ? '' : '이 학습 맥락에는 저장된 메모·하이라이트가 없습니다.');
    } catch {
      if (!current(n)) return;
      busy = false; items = []; more = null;
      draw('기록을 조회하지 못했습니다. 다시 조회해 주세요.');
    }
  }
  await loadContexts(false);
}
