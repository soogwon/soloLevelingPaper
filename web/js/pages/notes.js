// 4. 메모·하이라이트 페이지 — GET /v1/annotations (논문 구분 없이, 작성 날짜별, 페이지 번호 방식)
import { api } from '../api/index.js';
import { html, setHTML } from '../lib/html.js';
import { replaceQuery } from '../router.js';
import { icons, loadingBlock, emptyBlock, errorBlock, pagination } from '../lib/ui.js';
import { fullAnnotationCard } from '../lib/annotation.js';
import { dateKey, todayKey, shiftKey, isDateKey, formatDayHeading, formatKey } from '../lib/format.js';
import { pageFromQuery } from './papers.js';

const KINDS = [['', '전체'], ['note', '메모'], ['highlight', '하이라이트']];
const RANGES = [['today', '오늘'], ['week', '최근 7일'], ['all', '전체 기간']];

/** 선택한 기간 → 요청할 from/to (한국 날짜, 양 끝 포함: 요청서 4.4) */
function rangeToDates(state) {
  const today = todayKey();
  if (state.range === 'today') return { from: today, to: today };
  if (state.range === 'week') return { from: shiftKey(today, -6), to: today };
  if (state.range === 'custom') return { from: state.from, to: state.to };
  return {};
}

function rangeLabel(state) {
  const { from, to } = rangeToDates(state);
  if (!from) return '날짜 선택';
  return from === to ? formatKey(from) : `${formatKey(from)} – ${formatKey(to)}`;
}

/** 이번 페이지의 기록을 한국 날짜로 묶는다. */
function groupByDay(items) {
  const groups = [];
  const byKey = new Map();
  for (const a of items) {
    const key = dateKey(a.created_at) ?? 'unknown';
    if (!byKey.has(key)) { const g = { key, items: [] }; byKey.set(key, g); groups.push(g); }
    byKey.get(key).items.push(a);
  }
  return groups;
}

export default async function notesPage({ view, query }) {
  document.title = '메모·하이라이트 · Solo Leveling';
  const validRange = ['today', 'week', 'all', 'custom'].includes(query.range) ? query.range : 'all';
  const state = {
    kind: KINDS.some(([v]) => v === query.kind) ? query.kind : '',
    range: validRange === 'custom' && !(isDateKey(query.from) && isDateKey(query.to)) ? 'all' : validRange,
    from: query.from ?? '',
    to: query.to ?? '',
    sort: query.sort === 'oldest' ? 'oldest' : 'newest',
    q: (query.q ?? '').trim(),
    page: pageFromQuery(query.page),
    seq: 0,
    panelOpen: false,
  };

  setHTML(view, html`
    <div class="page-head">
      <div>
        <h1 class="page-title">메모·하이라이트</h1>
        <p class="page-desc">모든 논문에 남긴 기록을 작성한 날짜별로 모아 봐요. 날짜는 한국 시간 기준이에요.</p>
      </div>
    </div>
    ${state.q ? html`<p class="notice">‘${state.q}’ 검색 결과 <a href="#/notes">검색 지우기</a></p>` : ''}
    <div class="pills" role="group" aria-label="기록 종류">
      ${KINDS.map(([v, l]) => html`<button type="button" class="pill" data-kind="${v}" aria-pressed="${String(state.kind === v)}">${l}</button>`)}
    </div>
    <div class="filterbar">
      <span class="filterbar-label" id="date-label">작성 날짜</span>
      <button type="button" class="chip" data-action="toggle-panel" aria-expanded="false" aria-controls="date-panel">${icons.calendar()}<span data-slot="range-label"></span></button>
      <span class="pills" role="group" aria-labelledby="date-label">
        ${RANGES.map(([v, l]) => html`<button type="button" class="chip" data-range="${v}">${l}</button>`)}
      </span>
      <button type="button" class="ghost-btn" data-action="reset-date">${icons.reset()}날짜 초기화</button>
      <span class="filterbar-end">
        <label for="sort" class="meta">정렬</label>
        <select id="sort" class="select">
          <option value="newest">작성일 최신순</option>
          <option value="oldest">작성일 오래된순</option>
        </select>
      </span>
      <form class="date-panel" id="date-panel" hidden novalidate>
        <fieldset>
          <legend>선택 방식</legend>
          <button type="button" class="pill sm" data-mode="day" aria-pressed="true">하루</button>
          <button type="button" class="pill sm" data-mode="period" aria-pressed="false">기간</button>
        </fieldset>
        <label class="date-field">시작일<input type="date" name="from" required></label>
        <label class="date-field" data-slot="to-field" hidden>종료일<input type="date" name="to"></label>
        <button type="submit" class="btn btn-primary sm">적용</button>
        <p class="inline-error" data-slot="date-error" role="alert" hidden></p>
      </form>
    </div>
    <p class="meta" data-slot="summary" aria-live="polite"></p>
    <div class="stack" data-slot="groups" tabindex="-1"></div>
    <div data-slot="pager"></div>`);

  const groupsEl = view.querySelector('[data-slot="groups"]');
  const pagerEl = view.querySelector('[data-slot="pager"]');
  const summaryEl = view.querySelector('[data-slot="summary"]');
  const panel = view.querySelector('#date-panel');
  const panelToggle = view.querySelector('[data-action="toggle-panel"]');
  const sortSelect = view.querySelector('#sort');
  sortSelect.value = state.sort;
  let panelMode = 'day';

  function syncFilterUI() {
    view.querySelectorAll('[data-kind]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.kind === state.kind)));
    view.querySelectorAll('[data-range]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.range === state.range)));
    panelToggle.classList.toggle('is-on', state.range === 'custom');
    view.querySelector('[data-slot="range-label"]').textContent = rangeLabel(state);
    replaceQuery({
      kind: state.kind,
      range: state.range === 'all' ? '' : state.range,
      from: state.range === 'custom' ? state.from : '',
      to: state.range === 'custom' ? state.to : '',
      sort: state.sort === 'newest' ? '' : state.sort,
      q: state.q,
      page: state.page > 1 ? state.page : '',
    });
  }

  function render(res) {
    if (res.items.length === 0) {
      setHTML(groupsEl, emptyBlock('이 조건에 해당하는 기록이 없어요', '기간을 넓히거나 종류를 ‘전체’로 바꿔보세요.'));
    } else {
      // 묶음 옆 개수는 서버의 date_counts(필터 전체 기준)를 쓴다. 한 날짜가 두 페이지에 걸칠 수 있다.
      const counts = res.date_counts ?? {};
      setHTML(groupsEl, html`${groupByDay(res.items).map((g) => html`<section class="date-group" aria-labelledby="g-${g.key}">
        <div class="date-group-head">
          <h2 id="g-${g.key}">${g.key === 'unknown' ? '작성일 미상' : formatDayHeading(g.key)}</h2>
          <span class="meta">${counts[g.key] ?? g.items.length}개 기록</span>
        </div>
        ${g.key === 'unknown' ? html`<p class="meta">작성일이 남아 있지 않은 이전 기록이에요. 업로드한 날짜로 대신 표시하지 않아요.</p>` : ''}
        ${g.items.map((a) => fullAnnotationCard(a, { showDate: g.key === 'unknown' }))}
      </section>`)}`);
    }
    summaryEl.textContent = `조건에 맞는 기록 ${res.total_count}개 · ${state.sort === 'oldest' ? '작성일 오래된순' : '작성일 최신순'}`;
    setHTML(pagerEl, pagination({ page: res.page, totalPages: res.total_pages, totalCount: res.total_count, pageSize: res.page_size, label: '메모·하이라이트 페이지' }));
  }

  /** 필터를 바꾸면 1페이지부터 (요청서 1.5) */
  async function load(page, { moveFocus = false } = {}) {
    const seq = ++state.seq;
    state.page = page;
    syncFilterUI();
    setHTML(groupsEl, loadingBlock());
    summaryEl.textContent = '';
    try {
      const res = await api.listAnnotations({ kind: state.kind, ...rangeToDates(state), q: state.q, sort: state.sort, page });
      if (seq !== state.seq) return;
      if (res.items.length === 0 && page > 1 && res.total_count > 0) { load(res.total_pages, { moveFocus }); return; }
      render(res);
      if (moveFocus) {
        summaryEl.scrollIntoView({ block: 'start' });
        groupsEl.focus({ preventScroll: true });
      }
    } catch (error) {
      if (seq !== state.seq) return;
      setHTML(groupsEl, errorBlock(error, 'reload-list'));
      setHTML(pagerEl, '');
    }
  }

  function setPanel(open) {
    state.panelOpen = open;
    panel.hidden = !open;
    panelToggle.setAttribute('aria-expanded', String(open));
    if (open) {
      panel.elements.from.value = state.range === 'custom' ? state.from : todayKey();
      panel.elements.to.value = state.range === 'custom' ? state.to : todayKey();
      panel.elements.from.focus();
    }
  }

  function setMode(mode) {
    panelMode = mode;
    view.querySelectorAll('[data-mode]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
    view.querySelector('[data-slot="to-field"]').hidden = mode === 'day';
  }

  panel.addEventListener('submit', (e) => {
    e.preventDefault();
    const errorEl = view.querySelector('[data-slot="date-error"]');
    const from = panel.elements.from.value;
    const to = panelMode === 'day' ? from : panel.elements.to.value;
    let message = '';
    if (!isDateKey(from) || !isDateKey(to)) message = '날짜를 선택해 주세요.';
    else if (from > to) message = '시작일이 종료일보다 늦어요.';
    errorEl.hidden = !message;
    errorEl.textContent = message;
    if (message) return;
    Object.assign(state, { range: 'custom', from, to });
    setPanel(false);
    panelToggle.focus();
    load(1);
  });

  sortSelect.addEventListener('change', () => { state.sort = sortSelect.value; load(1); });

  view.addEventListener('click', (e) => {
    const kindBtn = e.target.closest('[data-kind]');
    if (kindBtn) { state.kind = kindBtn.dataset.kind; load(1); return; }
    const rangeBtn = e.target.closest('[data-range]');
    if (rangeBtn) { state.range = rangeBtn.dataset.range; setPanel(false); load(1); return; }
    const modeBtn = e.target.closest('[data-mode]');
    if (modeBtn) { setMode(modeBtn.dataset.mode); return; }
    const pageBtn = e.target.closest('[data-page]');
    if (pageBtn && !pageBtn.disabled) { load(Number(pageBtn.dataset.page), { moveFocus: true }); return; }
    switch (e.target.closest('[data-action]')?.dataset.action) {
      case 'toggle-panel': setPanel(!state.panelOpen); break;
      case 'reset-date': state.range = 'all'; setPanel(false); load(1); break; // 종류·검색 조건은 유지 (요청서 4.4)
      case 'reload-list': load(state.page); break;
      default: break;
    }
  });

  setMode(state.range === 'custom' && state.from !== state.to ? 'period' : 'day');
  await load(state.page);
}
