import { remoteAnnotationBadge } from './annotation.js';
import { request, ApiError } from '../api/client.js';
import { html, setHTML } from './html.js';
import { emptyBlock, errorBlock, loadingBlock } from './ui.js';
import { formatDate, formatDateTime, formatPosition, SUMMARY_KINDS } from './format.js';

const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const KINDS = [['', '전체'], ['note', '메모'], ['highlight', '하이라이트']];
const PAGE_SIZE = 3;

function validContext(item, paper, version) {
  return UUID.test(item?.context_id) && item.paper_id === paper
    && item.server_version_id === version && UUID.test(item.publication_id)
    && ['reading', 'completed'].includes(item.status)
    && Number.isSafeInteger(item.revision) && item.revision > 0
    && (item.last_position === null || (Number.isSafeInteger(item.last_position?.pdf_page)
      && item.last_position.pdf_page > 0))
    && typeof item.updated_at === 'string' && !Number.isNaN(Date.parse(item.updated_at));
}

function contextButtons(contexts, selected) {
  if (contexts.length <= 1) return '';
  return html`<div class="pills" role="group" aria-label="학습 맥락">
    ${contexts.map((item, index) => html`<button type="button" class="pill sm"
      data-remote-context="${item.context_id}" aria-pressed="${String(item.context_id === selected)}">
      학습 ${index + 1}
    </button>`)}
  </div>`;
}

function remoteSummaryContent(summary, metadata = {}) {
  if (!summary?.items?.length) {
    return emptyBlock('저장된 요약이 없어요', 'Claude에서 원격 요약을 저장하면 여기에 보여요.');
  }
  const brief = ['remote-summary-v2', 'remote-chat-summary-v1'].includes(summary.prompt_version);
  const kinds = brief
    ? [['claim', '연구 질문'], ['method', '핵심 접근'], ['result', '주요 결과']]
    : SUMMARY_KINDS;
  const sampled = metadata.total_scope_chunks > metadata.source_count;
  return html`${sampled ? html`<p class="hint">전체 근거 ${metadata.total_scope_chunks}개 중
    선택한 ${metadata.source_count}개를 바탕으로 요약했어요.</p>` : ''}
  ${kinds.map(([kind, label]) => {
    const items = summary.items.filter(item => item.kind === kind);
    if (!brief && !items.length) return '';
    return html`<div class="sum-item">
      <div class="sum-item-head"><h3>${label}</h3></div>
      ${items.length ? html`<ul>${items.map(item => {
        const pages = [...new Set(item.evidence_ids.map(id => metadata.evidence_pages?.[id])
          .filter(page => Number.isSafeInteger(page) && page > 0))].sort((a, b) => a - b);
        return html`<li>${item.text}<p class="hint">${pages.length ? `PDF ${pages.join(', ')}쪽 · ` : ''}
          근거 ${item.evidence_ids.length}개</p></li>`;
      })}</ul>` : html`<p class="hint">요약에 포함된 근거가 없어요.</p>`}
    </div>`;
  })}`;
}

function remoteAnnotationCard(annotation) {
  const origin = { user: '사용자 입력', ai: 'AI 생성', unknown: '확인되지 않음' }[annotation.creation_origin]
    ?? '확인되지 않음';
  return html`<article class="note-card" data-annotation-id="${annotation.annotation_id}">
    <div class="note-top">
      <span class="note-tags">${remoteAnnotationBadge(annotation)}<span class="badge b-gray">${origin}</span></span>
      <span class="note-pos">revision ${annotation.revision}</span>
    </div>
    ${(annotation.segments ?? []).map(segment => html`<div class="quote">
      <span class="badge b-src">${segment.target === 'translation' ? '한국어 번역' : '원문'} · 저장 당시 인용</span>
      <p class="quote-text">${segment.quote_snapshot}</p>
    </div>`)}
    ${annotation.body ? html`<p class="note-body">${annotation.kind === 'highlight' ? '메모 · ' : ''}${annotation.body}</p>` : ''}
    <span class="note-date">${annotation.updated_at ? `${formatDateTime(annotation.updated_at)} 저장` : '저장 시각 미확인'}</span>
  </article>`;
}

/** 논문 상세의 대표 원격 맥락·요약·기록을 한 선택 상태로 묶는다. */
export async function remotePaperDetail(views, paper, version) {
  if (!views?.progress || !views?.summary || !views?.notes || !UUID.test(paper)
      || typeof version !== 'string' || !version.trim() || version.length > 128) return;
  let closed = false, seq = 0, contexts = [], selected = null;
  let noteKind = '', notes = [], noteCursor = null, noteCount = 0, noteBusy = false;
  const events = ['auth:logout', 'auth:expired', 'hashchange', 'pagehide'];
  const active = value => !closed && views.progress.isConnected && seq === value;
  const close = () => {
    closed = true; ++seq; contexts = []; selected = null; notes = [];
    Object.values(views).forEach(view => { view.textContent = ''; });
    events.forEach(event => window.removeEventListener(event, close));
  };
  events.forEach(event => window.addEventListener(event, close));

  function current() { return contexts.find(item => item.context_id === selected) ?? null; }

  function bindContexts() {
    for (const view of [views.progress, views.summary, views.notes]) {
      view.querySelectorAll('[data-remote-context]').forEach(button => {
        button.onclick = () => select(button.dataset.remoteContext);
      });
    }
  }

  function drawProgress(message = '') {
    const context = current();
    if (!context) {
      setHTML(views.progress, emptyBlock('저장된 원격 학습 기록이 없어요', message || 'Claude에서 원격 학습을 시작하면 여기에 보여요.'));
      return;
    }
    setHTML(views.progress, html`<dl class="facts">
      <div class="fact"><dt>마지막 학습 위치</dt><dd>${formatPosition(context.last_position) ?? '학습 위치가 저장되지 않았어요'}</dd></div>
      <div class="fact"><dt>마지막 학습 기록</dt><dd>${formatDateTime(context.updated_at) || '기록 없음'}</dd></div>
      <div class="fact"><dt>학습 맥락</dt><dd class="normal">${contexts.length > 1
        ? contextButtons(contexts, selected) : context.status === 'completed' ? '읽기 완료' : '읽는 중'}</dd></div>
    </dl>`);
    bindContexts();
  }

  function drawSummary(content, message = '') {
    const context = current();
    setHTML(views.summary, html`<div class="panel-head"><h2 class="panel-title">요약</h2>
      ${context ? html`<span class="hint">${formatDate(context.updated_at)} 학습 맥락</span>` : ''}</div>
      ${message ? html`<p role="status" class="hint">${message}</p>` : ''}${content}`);
    const retry = views.summary.querySelector('[data-action="reload-remote-summary"]');
    if (retry && context) retry.onclick = () => loadSummary(context.context_id);
  }

  function drawNotes(message = '') {
    const visible = notes.filter(item => !noteKind || item.kind === noteKind);
    setHTML(views.notes, html`<h2 class="panel-title">메모·하이라이트 <small>${noteCount}개</small></h2>
      <div class="pills" role="group" aria-label="기록 종류">
        ${KINDS.map(([value, label]) => html`<button type="button" class="pill sm" data-remote-kind="${value}"
          aria-pressed="${String(value === noteKind)}" ${noteBusy ? 'disabled' : ''}>${label}</button>`)}</div>
      ${message ? html`<p role="status" class="hint">${message}</p>` : ''}
      <div class="stack">${visible.length ? visible.map(remoteAnnotationCard)
        : emptyBlock(noteKind ? '이 종류의 기록이 없어요' : '아직 남긴 기록이 없어요', 'Claude에서 메모나 하이라이트를 저장하면 여기에 보여요.')}</div>
      ${noteCursor ? html`<button type="button" class="btn sm" data-remote-notes-more ${noteBusy ? 'disabled' : ''}>기록 더 보기</button>` : ''}`);
    views.notes.querySelectorAll('[data-remote-kind]').forEach(button => {
      button.onclick = () => { noteKind = button.dataset.remoteKind; loadNotes(false); };
    });
    const more = views.notes.querySelector('[data-remote-notes-more]');
    if (more) more.onclick = () => loadNotes(true);
  }

  async function loadSummary(contextId) {
    const n = seq;
    drawSummary(loadingBlock('요약을 불러오는 중이에요'));
    try {
      const value = await request('GET', `/remote-summaries/contexts/${contextId}/latest`);
      if (!active(n) || selected !== contextId) return;
      const result = value?.result, binding = result?.binding;
      if (value?.state !== 'completed' || value.context_id !== contextId || result?.context_id !== contextId
          || binding?.context_id !== contextId || binding.paper_id !== paper
          || binding.server_version_id !== version || binding.publication_id !== current()?.publication_id
          || !Array.isArray(result.items)
          || result.items.some(item => !SUMMARY_KINDS.some(([kind]) => kind === item?.kind)
            || typeof item.text !== 'string' || !Array.isArray(item.evidence_ids)
            || !item.evidence_ids.every(id => UUID.test(id)))) throw Error('scope');
      drawSummary(remoteSummaryContent(result, value));
    } catch (error) {
      if (!active(n) || selected !== contextId) return;
      if (error instanceof ApiError && error.status === 404) drawSummary(remoteSummaryContent(null));
      else drawSummary(errorBlock(error, 'reload-remote-summary'));
    }
  }

  async function loadNotes(append) {
    const contextId = selected;
    if (!contextId || noteBusy) return;
    noteBusy = true; const n = seq;
    if (!append) { notes = []; noteCursor = null; }
    drawNotes('기록을 불러오는 중이에요.');
    try {
      const page = await request('GET', `/remote-annotation-products/contexts/${contextId}/annotations`, {
        query: { after: append ? noteCursor : undefined, limit: PAGE_SIZE, kind: noteKind || undefined },
      });
      if (!active(n) || selected !== contextId) return;
      if (!Array.isArray(page.items) || !Number.isSafeInteger(page.total_count) || page.total_count < 0
          || (page.next_cursor !== null && !UUID.test(page.next_cursor))
          || page.items.some(item => item.context_id !== contextId || !UUID.test(item.annotation_id)
            || !['note', 'highlight'].includes(item.kind) || !Number.isSafeInteger(item.revision)
            || !Array.isArray(item.segments))) throw Error('scope');
      notes = append ? [...new Map([...notes, ...page.items].map(item => [item.annotation_id, item])).values()] : page.items;
      noteCursor = page.next_cursor; noteCount = page.total_count; noteBusy = false; drawNotes();
    } catch (error) {
      if (!active(n) || selected !== contextId) return;
      noteBusy = false; notes = []; noteCursor = null; noteCount = 0;
      setHTML(views.notes, html`<h2 class="panel-title">메모·하이라이트</h2>${errorBlock(error, 'reload-remote-notes')}`);
      const retry = views.notes.querySelector('[data-action="reload-remote-notes"]');
      if (retry) retry.onclick = () => loadNotes(false);
    }
  }

  async function select(contextId) {
    if (!contexts.some(item => item.context_id === contextId)) return;
    ++seq; selected = contextId; noteKind = ''; notes = []; noteCursor = null; noteCount = 0; noteBusy = false;
    drawProgress();
    await Promise.all([loadSummary(contextId), loadNotes(false)]);
  }

  setHTML(views.progress, loadingBlock('학습 기록을 불러오는 중이에요'));
  drawSummary(loadingBlock());
  setHTML(views.notes, loadingBlock());
  const n = ++seq;
  try {
    const page = await request('GET', '/remote-annotation-products/contexts', {
      query: { paper_id: paper, version_id: version, limit: 50 },
    });
    if (!active(n)) return;
    if (!Array.isArray(page.items) || page.items.some(item => !validContext(item, paper, version))) throw Error('scope');
    contexts = page.items;
    if (!contexts.length) {
      drawProgress(); drawSummary(remoteSummaryContent(null)); drawNotes(); return;
    }
    await select(contexts[0].context_id);
  } catch (error) {
    if (!active(n)) return;
    setHTML(views.progress, errorBlock(error, 'reload-remote-detail'));
    drawSummary(remoteSummaryContent(null), '원격 학습 맥락을 확인하지 못했습니다.');
    drawNotes('원격 학습 맥락을 확인하지 못했습니다.');
  }
}
