// 2. 논문 목록 페이지 — GET /v1/papers (페이지 번호 방식)
import { api } from '../api/index.js';
import { html, setHTML } from '../lib/html.js';
import { buildHash, replaceQuery } from '../router.js';
import { icons, statusBadge, loadingBlock, emptyBlock, errorBlock, pagination } from '../lib/ui.js';
import { formatAuthorsYear, formatPosition, formatDate, paperTitle } from '../lib/format.js';

const FILTERS = [['', '전체'], ['reading', '읽는 중'], ['completed', '읽기 완료']];

export function paperRow(p) {
  const href = buildHash(`/papers/${encodeURIComponent(p.paper_id)}`, {
    record: p.selected_learning_record_id,
    version: p.selected_version_id,
  });
  const position = formatPosition(p.last_position);
  const date = formatDate(p.last_studied_at);
  let line = position;
  if (!line) {
    if (date) line = '학습 위치가 저장되지 않았어요';
    else if (p.selected_learning_record_id) line = '아직 저장된 학습 위치가 없어요';
    else line = '아직 저장된 학습 기록이 없어요';
  }
  if (date) line += ` · 마지막 학습 ${date}`;

  return html`<a class="row" href="${href}">
    <span class="row-icon">${icons.file()}</span>
    <span class="row-main">
      <span class="row-title">${paperTitle(p)}</span>
      <span class="row-sub">${formatAuthorsYear(p) ?? '저자·출판 연도 정보 없음'}</span>
      <span class="row-line">${line}</span>
    </span>
    ${statusBadge(p.reading_status)}
  </a>`;
}

export function pageFromQuery(value) {
  const n = Number(value);
  return Number.isInteger(n) && n >= 1 ? n : 1;
}

export default async function papersPage({ view, query }) {
  document.title = '논문 아카이브 · Solo Leveling';
  const state = {
    status: FILTERS.some(([v]) => v === query.status) ? query.status : '',
    q: (query.q ?? '').trim(),
    page: pageFromQuery(query.page),
    seq: 0,
  };

  setHTML(view, html`
    <div class="page-head">
      <div>
        <h1 class="page-title">논문 아카이브</h1>
        <p class="page-desc">Claude에서 학습한 논문과 마지막으로 읽던 위치를 다시 확인하세요.</p>
      </div>
    </div>
    ${state.q ? html`<p class="notice">‘${state.q}’ 제목 검색 결과 <a href="#/papers">검색 지우기</a></p>` : ''}
    <div class="toolbar" data-slot="top">
      <div class="pills" role="group" aria-label="읽기 상태 필터">
        ${FILTERS.map(([value, label]) => html`<button type="button" class="pill" data-status="${value}" aria-pressed="${String(state.status === value)}">${label}</button>`)}
      </div>
      <p class="meta" data-slot="count" aria-live="polite"></p>
    </div>
    <section class="card list" aria-label="논문 목록" tabindex="-1" data-slot="list"></section>
    <div data-slot="pager"></div>`);

  const listEl = view.querySelector('[data-slot="list"]');
  const pagerEl = view.querySelector('[data-slot="pager"]');
  const countEl = view.querySelector('[data-slot="count"]');

  async function load(page, { moveFocus = false } = {}) {
    const seq = ++state.seq;
    state.page = page;
    replaceQuery({ status: state.status, q: state.q, page: page > 1 ? page : '' });
    setHTML(listEl, loadingBlock());
    try {
      const res = await api.listPapers({ reading_status: state.status, q: state.q, page });
      if (seq !== state.seq) return;
      // 필터가 바뀌어 페이지 수가 줄었으면 마지막 페이지로 다시 요청한다.
      if (res.items.length === 0 && page > 1 && res.total_count > 0) { load(res.total_pages, { moveFocus }); return; }

      if (res.items.length === 0) {
        setHTML(listEl, state.q || state.status
          ? emptyBlock('조건에 맞는 논문이 없어요', '다른 상태를 선택하거나 검색어를 바꿔보세요.')
          : emptyBlock('아직 저장된 논문이 없어요', 'Claude에서 논문을 학습하고 서버 저장에 동의하면 여기에 보여요.'));
      } else {
        setHTML(listEl, html`${res.items.map(paperRow)}`);
      }
      countEl.textContent = `총 ${res.total_count}편 · 최근 학습순`;
      setHTML(pagerEl, pagination({ page: res.page, totalPages: res.total_pages, totalCount: res.total_count, pageSize: res.page_size, label: '논문 목록 페이지' }));
      if (moveFocus) {
        view.querySelector('[data-slot="top"]').scrollIntoView({ block: 'start' });
        listEl.focus({ preventScroll: true });
      }
    } catch (error) {
      if (seq !== state.seq) return;
      setHTML(listEl, errorBlock(error, 'reload-list'));
      setHTML(pagerEl, '');
    }
  }

  view.addEventListener('click', (e) => {
    const pill = e.target.closest('[data-status]');
    if (pill) {
      state.status = pill.dataset.status;
      view.querySelectorAll('[data-status]').forEach((b) => b.setAttribute('aria-pressed', String(b === pill)));
      load(1);
      return;
    }
    const pageBtn = e.target.closest('[data-page]');
    if (pageBtn && !pageBtn.disabled) { load(Number(pageBtn.dataset.page), { moveFocus: true }); return; }
    if (e.target.closest('[data-action="reload-list"]')) load(state.page);
  });

  await load(state.page);
}
