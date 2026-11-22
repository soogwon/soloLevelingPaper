// 2. 논문 목록 페이지 — GET /v1/papers (페이지 번호 방식)
import { api } from '../api/index.js';
import { html, setHTML } from '../lib/html.js';
import { buildHash, replaceQuery } from '../router.js';
import { icons, statusBadge, loadingBlock, emptyBlock, errorBlock, pagination, toast, errorMessage } from '../lib/ui.js';
import { runPaperDeletionFlow, deletionAcceptedMessage } from '../lib/paper-deletion.js';
import { formatAuthorsYear, formatPosition, formatDateTime, paperTitle, paperFileNameHint } from '../lib/format.js';

const FILTERS = [['', '전체'], ['reading', '읽는 중'], ['completed', '읽기 완료']];

// 정렬 기준과 처음 고를 때의 방향. 서버도 같은 기본 방향을 쓴다.
export const SORTS = {
  recent: { label: '최근 학습순', defaultOrder: 'desc' },
  title: { label: '제목순', defaultOrder: 'asc' },
  status: { label: '읽기 상태순', defaultOrder: 'asc' },
  updated: { label: '갱신 시각순', defaultOrder: 'desc' },
};
const ORDER_LABEL = { asc: '오름차순', desc: '내림차순' };

export function sortFromQuery(query) {
  const sort = Object.hasOwn(SORTS, query.sort ?? '') ? query.sort : 'recent';
  const order = query.order === 'asc' || query.order === 'desc' ? query.order : SORTS[sort].defaultOrder;
  return { sort, order };
}

/** 같은 열을 다시 누르면 방향만 뒤집고, 다른 열을 누르면 그 열의 기본 방향으로 시작한다. */
export function nextSort(current, sort) {
  if (current.sort === sort) return { sort, order: current.order === 'asc' ? 'desc' : 'asc' };
  return { sort, order: SORTS[sort].defaultOrder };
}

function sortHeader(state, sort, label) {
  const active = state.sort === sort;
  const arrow = active ? (state.order === 'asc' ? '↑' : '↓') : '↕';
  const next = nextSort(state, sort);
  return html`<button type="button" class="col-sort${active ? ' is-active' : ''}" data-sort="${sort}"
    aria-label="${label} ${ORDER_LABEL[next.order]} 정렬${active ? ` (현재 ${ORDER_LABEL[state.order]})` : ''}">
    ${label}<span class="sort-arrow" aria-hidden="true">${arrow}</span></button>`;
}

/** 목록 위 열 이름. 행과 같은 칸 구성을 써서 열이 맞는다. */
export function paperListHead(state) {
  return html`<div class="row-item list-head">
    <div class="row head-row">
      <span class="row-icon head-spacer" aria-hidden="true"></span>
      <span class="row-main">${sortHeader(state, 'title', '논문')}</span>
      <span class="row-status">${sortHeader(state, 'status', '읽기 상태')}</span>
      <span class="row-processing col-label">처리 상태</span>
      <span class="row-basis col-label">상태 근거 · 기준 버전</span>
      <span class="row-updated">${sortHeader(state, 'recent', '학습 시각')}</span>
    </div>
    <span class="row-menu head-spacer menu-spacer" aria-hidden="true"></span>
  </div>`;
}

// 서버 처리(파싱·번역·색인) 상태. 읽기 상태와 별개로, 학습 자료가 준비됐는지 보여 준다.
const PROCESSING = {
  none: ['처리 작업 없음', 'b-unknown'],
  queued: ['처리 대기', 'b-not_started'],
  running: ['처리 중', 'b-reading'],
  completed: ['처리 완료', 'b-completed'],
  failed: ['처리 실패', 'b-failed'],
  cancelled: ['처리 취소', 'b-not_started'],
};

export function processingBadge(status) {
  const [label, cls] = PROCESSING[status] ?? PROCESSING.none;
  return html`<span class="badge ${cls}">${label}</span>`;
}

export function paperRow(p) {
  const href = buildHash(`/papers/${encodeURIComponent(p.paper_id)}`, {
    record: p.selected_learning_record_id,
    version: p.selected_version_id,
  });
  const position = formatPosition(p.last_position);
  let line = position;
  if (!line) {
    if (p.last_studied_at) line = '학습 위치가 저장되지 않았어요';
    else if (p.selected_learning_record_id) line = '아직 저장된 학습 위치가 없어요';
    else if (p.reading_status_source === 'remote_learning') line = 'Claude 원격 학습 기록이 있어요';
    else line = '아직 저장된 학습 기록이 없어요';
  }
  // 이름 아래에는 읽기 상태 갱신 시각을, 오른쪽 열에는 학습 시각(최근 학습순 정렬 기준)을 둔다.
  const updated = formatDateTime(p.reading_status_updated_at);
  const learnedAt = formatDateTime(p.last_learning_at ?? p.last_studied_at);
  const source = {
    explicit: '연결 PC에서 저장한 상태',
    remote_learning: '원격 학습 기록',
    remote_file: '등록된 원격 원문',
    unknown: '상태 근거 미확인',
  }[p.reading_status_source] ?? '상태 근거 미확인';

  return html`<a class="row" href="${href}">
    <span class="row-icon">${icons.file()}</span>
    <span class="row-main">
      <span class="row-title">${paperTitle(p)}</span>
      <span class="row-sub">${[formatAuthorsYear(p) ?? '저자·출판 연도 정보 없음', paperFileNameHint(p)].filter(Boolean).join(' · ')}</span>
      <span class="row-line">${line}${updated ? html` · <span class="nowrap">갱신 시각 ${updated}</span>` : ''}</span>
    </span>
    <span class="row-status">${statusBadge(p.reading_status)}</span>
    <span class="row-processing"><span class="cell-label">처리 상태: </span>${processingBadge(p.processing_status)}</span>
    <span class="row-basis"><span class="cell-label">상태 근거: </span>${source}${p.reading_basis_version_id ? ` · 기준 버전: ${p.reading_basis_version_id}` : ''}</span>
    <span class="row-updated"><span class="cell-label">학습 시각: </span>${learnedAt || '기록 없음'}</span>
  </a>`;
}

/** 목록 화면 전용 행: 링크 옆에 행별 메뉴(삭제)를 둔다. 검색 화면은 메뉴 없는 paperRow를 그대로 쓴다. */
export function paperRowWithMenu(p) {
  const title = paperTitle(p);
  return html`<div class="row-item" data-paper-id="${p.paper_id}">
    ${paperRow(p)}
    <details class="menu-wrap row-menu">
      <summary class="icon-btn" aria-label="${title} 메뉴">${icons.more()}</summary>
      <div class="menu">
        <button type="button" class="menu-item danger" data-action="delete-paper" data-paper-id="${p.paper_id}" data-title="${title}">서버에서 논문 삭제</button>
      </div>
    </details>
  </div>`;
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
    ...sortFromQuery(query),
    seq: 0,
  };

  setHTML(view, html`
    <div class="page-head papers-head">
      <div class="papers-head-copy">
        <h1 class="page-title">논문 아카이브</h1>
        <p class="page-desc">Claude에서 학습한 논문과 마지막으로 읽던 위치를 다시 확인하세요.</p>
        <p class="hint">읽기 상태는 사용자의 학습 상태이며 PDF 업로드·처리 상태와는 별개예요. 새 PDF의 처리 진행은 ‘새 PDF 등록 준비’에서 확인하세요.</p>
      </div>
      <div class="actions papers-head-actions" aria-label="논문 아카이브 작업">
        <a class="btn" href="#/uploads">새 PDF 등록 준비</a>
        <a class="btn" href="#/remote-learning">원격 학습 현황</a>
      </div>
    </div>
    ${state.q ? html`<p class="notice">‘${state.q}’ 제목 검색 결과 <a href="#/papers">검색 지우기</a></p>` : ''}
    <div class="toolbar" data-slot="top">
      <div class="pills" role="group" aria-label="읽기 상태 필터">
        ${FILTERS.map(([value, label]) => html`<button type="button" class="pill" data-status="${value}" aria-pressed="${String(state.status === value)}">${label}</button>`)}
      </div>
      <span class="toolbar-end">
        <p class="meta" data-slot="count" aria-live="polite"></p>
        <label for="paper-sort" class="sr-only">정렬 기준</label>
        <select id="paper-sort" class="select">
          ${Object.entries(SORTS).map(([value, { label }]) => html`<option value="${value}">${label}</option>`)}
        </select>
        <button type="button" class="ghost-btn" data-action="toggle-order" aria-label="정렬 방향 바꾸기"></button>
      </span>
    </div>
    <section class="card list" aria-label="논문 목록" tabindex="-1" data-slot="list"></section>
    <div data-slot="pager"></div>`);

  const listEl = view.querySelector('[data-slot="list"]');
  const pagerEl = view.querySelector('[data-slot="pager"]');
  const countEl = view.querySelector('[data-slot="count"]');
  const sortSelect = view.querySelector('#paper-sort');
  const orderBtn = view.querySelector('[data-action="toggle-order"]');

  function syncSortControls() {
    sortSelect.value = state.sort;
    orderBtn.textContent = state.order === 'asc' ? '↑ 오름차순' : '↓ 내림차순';
  }
  syncSortControls();

  function applySort(next) {
    state.sort = next.sort;
    state.order = next.order;
    syncSortControls();
    load(1);
  }

  async function load(page, { moveFocus = false } = {}) {
    const seq = ++state.seq;
    state.page = page;
    replaceQuery({
      status: state.status, q: state.q,
      sort: state.sort === 'recent' ? '' : state.sort,
      order: state.order === SORTS[state.sort].defaultOrder ? '' : state.order,
      page: page > 1 ? page : '',
    });
    setHTML(listEl, loadingBlock());
    try {
      const res = await api.listPapers({ reading_status: state.status, q: state.q, sort: state.sort, order: state.order, page });
      if (seq !== state.seq) return;
      // 필터가 바뀌어 페이지 수가 줄었으면 마지막 페이지로 다시 요청한다.
      if (res.items.length === 0 && page > 1 && res.total_count > 0) { load(res.total_pages, { moveFocus }); return; }

      if (res.items.length === 0) {
        setHTML(listEl, state.q || state.status
          ? emptyBlock('조건에 맞는 논문이 없어요', '다른 상태를 선택하거나 검색어를 바꿔보세요.')
          : emptyBlock('아직 저장된 논문이 없어요', 'Claude에서 논문을 학습하고 서버 저장에 동의하면 여기에 보여요.'));
      } else {
        setHTML(listEl, html`${paperListHead(state)}${res.items.map((p) => paperRowWithMenu(p))}`);
      }
      countEl.textContent = `총 ${res.total_count}편`;
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
    const sortBtn = e.target.closest('[data-sort]');
    if (sortBtn) { applySort(nextSort(state, sortBtn.dataset.sort)); return; }
    if (e.target.closest('[data-action="toggle-order"]')) {
      applySort({ sort: state.sort, order: state.order === 'asc' ? 'desc' : 'asc' });
      return;
    }
    const pageBtn = e.target.closest('[data-page]');
    if (pageBtn && !pageBtn.disabled) { load(Number(pageBtn.dataset.page), { moveFocus: true }); return; }
    if (e.target.closest('[data-action="reload-list"]')) { load(state.page); return; }
    const del = e.target.closest('[data-action="delete-paper"]');
    if (del) deletePaper(del);
  });

  sortSelect.addEventListener('change', () => {
    applySort({ sort: sortSelect.value, order: SORTS[sortSelect.value].defaultOrder });
  });

  // 여러 논문을 차례로 정리할 수 있게 목록에 머문 채 현재 페이지만 다시 불러온다.
  async function deletePaper(button) {
    const row = button.closest('.row-item');
    button.closest('details')?.removeAttribute('open');
    row?.setAttribute('aria-busy', 'true');
    try {
      const result = await runPaperDeletionFlow({ paperId: button.dataset.paperId, title: button.dataset.title });
      if (!result) return;
      toast(deletionAcceptedMessage(result));
      await load(state.page);
    } catch (error) {
      toast(errorMessage(error));
    } finally {
      if (row?.isConnected) row.removeAttribute('aria-busy');
    }
  }

  await load(state.page);
}
