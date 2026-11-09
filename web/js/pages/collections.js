// 5. 컬렉션 목록 페이지 — GET /v1/collections (페이지 번호 방식), POST /v1/collections
import { api } from '../api/index.js';
import { html, setHTML } from '../lib/html.js';
import { navigate, replaceQuery } from '../router.js';
import { icons, loadingBlock, errorBlock, pagination, newRequestKey } from '../lib/ui.js';
import { openNameDialog } from './collection-form.js';
import { pageFromQuery } from './papers.js';

/** 카드에는 이름과 논문 수만 표시한다 (요청서 5.1). */
function collectionCard(c) {
  const count = c.paper_count > 0 ? `논문 ${c.paper_count}편` : '비어 있음';
  return html`<a class="ccard" href="#/collections/${encodeURIComponent(c.collection_id)}">
    <span class="ccard-icon ${c.paper_count > 0 ? '' : 'empty'}">${icons.folder(22)}</span>
    <span class="ccard-text"><span class="ccard-name">${c.name}</span><span class="ccard-count">${count}</span></span>
  </a>`;
}

export default async function collectionsPage({ view, query }) {
  document.title = '연구 컬렉션 · Solo Leveling';
  const state = { page: pageFromQuery(query.page), seq: 0 };

  setHTML(view, html`
    <div class="page-head">
      <div>
        <h1 class="page-title">연구 컬렉션</h1>
        <p class="page-desc">프로젝트나 관심 주제별로 논문을 폴더에 정리하세요.</p>
      </div>
      <button type="button" class="btn btn-primary" data-action="create">${icons.plus()}새 컬렉션</button>
    </div>
    <p class="meta" data-slot="count" aria-live="polite"></p>
    <div data-slot="grid" tabindex="-1"></div>
    <div data-slot="pager"></div>
    <p class="meta">논문 하나는 컬렉션 한 곳에만 넣을 수 있어요. 컬렉션을 삭제하거나 논문을 빼도 논문, 학습 기록, 메모, PDF는 그대로 남아요.</p>`);

  const gridEl = view.querySelector('[data-slot="grid"]');
  const pagerEl = view.querySelector('[data-slot="pager"]');
  const countEl = view.querySelector('[data-slot="count"]');

  async function load(page, { moveFocus = false } = {}) {
    const seq = ++state.seq;
    state.page = page;
    replaceQuery({ page: page > 1 ? page : '' });
    setHTML(gridEl, loadingBlock());
    try {
      const res = await api.listCollections({ page });
      if (seq !== state.seq) return;
      if (res.items.length === 0 && page > 1 && res.total_count > 0) { load(res.total_pages, { moveFocus }); return; }

      setHTML(gridEl, html`<div class="grid-cards">
        ${res.items.map(collectionCard)}
        <button type="button" class="newcard" data-action="create">${icons.plus(22)}새 컬렉션 만들기</button>
      </div>
      ${res.total_count === 0 ? html`<p class="meta" style="margin-top:12px">아직 만든 컬렉션이 없어요.</p>` : ''}`);
      countEl.textContent = res.total_count ? `컬렉션 ${res.total_count}개 · 최근 만든 순` : '';
      setHTML(pagerEl, pagination({ page: res.page, totalPages: res.total_pages, totalCount: res.total_count, pageSize: res.page_size, label: '컬렉션 목록 페이지' }));
      if (moveFocus) {
        view.scrollIntoView({ block: 'start' });
        gridEl.focus({ preventScroll: true });
      }
    } catch (error) {
      if (seq !== state.seq) return;
      setHTML(gridEl, errorBlock(error, 'reload-list'));
      setHTML(pagerEl, '');
    }
  }

  function create() {
    // 같은 대화상자에서의 재시도는 같은 요청 키를 써서 중복 생성을 막는다 (요청서 5.3).
    const key = newRequestKey();
    openNameDialog({
      title: '새 컬렉션',
      submitLabel: '만들기',
      onSubmit: async (name) => {
        const created = await api.createCollection(name, key);
        navigate(`/collections/${encodeURIComponent(created.collection_id)}`);
      },
    });
  }

  view.addEventListener('click', (e) => {
    const pageBtn = e.target.closest('[data-page]');
    if (pageBtn && !pageBtn.disabled) { load(Number(pageBtn.dataset.page), { moveFocus: true }); return; }
    switch (e.target.closest('[data-action]')?.dataset.action) {
      case 'create': create(); break;
      case 'reload-list': load(state.page); break;
      default: break;
    }
  });

  await load(state.page);
}
