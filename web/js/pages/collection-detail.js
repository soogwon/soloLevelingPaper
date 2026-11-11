// 6. 컬렉션 상세 페이지
// GET /v1/collections/{id}, GET /v1/collections/{id}/papers (페이지 번호 방식), PATCH·DELETE /v1/collections/{id},
// GET /v1/papers?unassigned=true, PUT·DELETE /v1/collections/{id}/papers/{paper_id}
import { CONFIG } from '../config.js';
import { api } from '../api/index.js';
import { html, setHTML } from '../lib/html.js';
import { navigate, buildHash, replaceQuery } from '../router.js';
import {
  icons, loadingBlock, emptyBlock, errorBlock, pagination, errorMessage, openDialog, confirmDialog, toast,
} from '../lib/ui.js';
import { formatAuthors, formatPublished, paperTitle } from '../lib/format.js';
import { openNameDialog } from './collection-form.js';
import { pageFromQuery } from './papers.js';

// 논문 추가 대화상자도 다른 목록과 같은 개수로 보여준다.
const ADD_PAGE_SIZE = CONFIG.PAGE_SIZE;

/** 목록에는 제목·저자·발행일만 표시한다 (요청서 6.1, 6.7). */
function paperRowHtml(p) {
  const href = buildHash(`/papers/${encodeURIComponent(p.paper_id)}`);
  return html`<div class="tr" data-paper-id="${p.paper_id}">
    <a class="tr-title" href="${href}">${icons.file()}${paperTitle(p)}</a>
    <span class="tr-cell">${formatAuthors(p) ?? '정보 없음'}</span>
    <span class="tr-cell">${formatPublished(p.published) ?? '정보 없음'}</span>
    <details class="menu-wrap">
      <summary class="icon-btn" aria-label="${paperTitle(p)} 메뉴">${icons.more()}</summary>
      <div class="menu">
        <a class="menu-item" href="${href}">논문 상세 보기</a>
        <button type="button" class="menu-item danger" data-action="remove" data-paper-id="${p.paper_id}">컬렉션에서 빼기</button>
      </div>
    </details>
  </div>`;
}

export default async function collectionDetailPage({ view, params, query }) {
  const collectionId = params.collectionId;
  let collection = await api.getCollection(collectionId);
  const state = { page: pageFromQuery(query.page), seq: 0 };

  setHTML(view, html`
    <nav class="breadcrumb" aria-label="현재 위치"><a href="#/collections">컬렉션</a><span aria-hidden="true">/</span><span data-slot="crumb"></span></nav>
    <div class="page-head" style="align-items:flex-start">
      <div>
        <h1 class="page-title" data-slot="name"></h1>
        <p class="page-desc" data-slot="count"></p>
      </div>
      <div class="actions" style="align-items:center">
        <button type="button" class="btn" data-action="rename">이름 수정</button>
        <button type="button" class="btn btn-primary" data-action="add">${icons.plus()}논문 추가</button>
        <details class="menu-wrap">
          <summary class="icon-btn" aria-label="컬렉션 메뉴">${icons.more()}</summary>
          <div class="menu"><button type="button" class="menu-item danger" data-action="delete">컬렉션 삭제</button></div>
        </details>
      </div>
    </div>
    <section class="card table-card" aria-label="포함된 논문" tabindex="-1" data-slot="table">
      <div class="table">
        <div class="tr th"><span>논문 제목</span><span>저자</span><span>발행 연도</span><span class="sr-only">메뉴</span></div>
        <div data-slot="rows"></div>
      </div>
    </section>
    <div data-slot="pager"></div>
    <p class="meta">논문을 누르면 저장된 학습 기록으로 이동해요. 컬렉션에서 빼도 논문과 학습 기록, 메모는 그대로 남아요.</p>`);

  const rowsEl = view.querySelector('[data-slot="rows"]');
  const pagerEl = view.querySelector('[data-slot="pager"]');
  const tableEl = view.querySelector('[data-slot="table"]');

  function renderHeader() {
    document.title = `${collection.name} · Solo Leveling`;
    view.querySelector('[data-slot="name"]').textContent = collection.name;
    view.querySelector('[data-slot="crumb"]').textContent = collection.name;
    view.querySelector('[data-slot="count"]').textContent = `논문 ${collection.paper_count}편`;
  }

  async function load(page, { moveFocus = false } = {}) {
    const seq = ++state.seq;
    state.page = page;
    replaceQuery({ page: page > 1 ? page : '' });
    setHTML(rowsEl, loadingBlock());
    try {
      const res = await api.listCollectionPapers(collectionId, { page });
      if (seq !== state.seq) return;
      // 논문을 빼서 마지막 페이지가 비면 그 앞 페이지로 다시 요청한다.
      if (res.items.length === 0 && page > 1 && res.total_count > 0) { load(res.total_pages, { moveFocus }); return; }
      collection = { ...collection, paper_count: res.total_count };
      renderHeader();
      setHTML(rowsEl, res.items.length
        ? html`${res.items.map(paperRowHtml)}`
        : emptyBlock('아직 논문이 없어요', '‘논문 추가’로 어느 컬렉션에도 없는 논문을 넣어보세요.'));
      setHTML(pagerEl, pagination({ page: res.page, totalPages: res.total_pages, totalCount: res.total_count, pageSize: res.page_size, label: '포함된 논문 페이지' }));
      if (moveFocus) {
        tableEl.scrollIntoView({ block: 'start' });
        tableEl.focus({ preventScroll: true });
      }
    } catch (error) {
      if (seq !== state.seq) return;
      setHTML(rowsEl, errorBlock(error, 'reload-list'));
      setHTML(pagerEl, '');
    }
  }

  function rename() {
    openNameDialog({
      title: '컬렉션 이름 수정',
      submitLabel: '저장',
      initial: collection.name,
      onSubmit: async (name) => {
        collection = await api.renameCollection(collectionId, name);
        renderHeader();
        toast('이름을 바꿨어요.');
      },
    });
  }

  async function remove() {
    const ok = await confirmDialog({
      title: `‘${collection.name}’을(를) 삭제할까요?`,
      message: `폴더만 삭제돼요. 들어 있던 논문 ${collection.paper_count}편은 미분류로 바뀌고, 논문·학습 기록·메모·PDF는 그대로 남아요.`,
      confirmLabel: '컬렉션 삭제',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteCollection(collectionId);
      toast('컬렉션을 삭제했어요.');
      // 삭제한 컬렉션 화면으로 '뒤로' 돌아오지 않도록 현재 기록을 바꾼다.
      navigate('/collections', undefined, { replace: true });
    } catch (error) {
      toast(errorMessage(error));
    }
  }

  async function removePaper(button) {
    const paperId = button.dataset.paperId;
    button.closest('details')?.removeAttribute('open');
    try {
      await api.removePaperFromCollection(collectionId, paperId);
      toast('컬렉션에서 뺐어요.');
      // 같은 페이지를 다시 불러와 다음 페이지의 논문이 올라오게 한다.
      await load(state.page);
    } catch (error) {
      toast(errorMessage(error));
    }
  }

  // 논문 추가: 어떤 컬렉션에도 속하지 않은 논문만 후보로 보여준다 (요청서 6.4).
  function openAdd() {
    let changed = false;
    const dialog = openDialog({
      title: '논문 추가',
      size: 'lg',
      body: html`
        <p class="dialog-text">어느 컬렉션에도 없는 논문만 보여요.</p>
        <form class="search" role="search" data-slot="add-search">
          <label for="add-q" class="sr-only">추가할 논문 제목 검색</label>
          ${icons.search()}
          <input id="add-q" name="q" type="search" placeholder="논문 제목 검색" autocomplete="off">
        </form>
        <div data-slot="candidates"></div>
        <div data-slot="add-pager"></div>
        <div class="dialog-actions"><button type="button" class="btn" data-dialog-close>닫기</button></div>`,
    });
    const listEl = dialog.querySelector('[data-slot="candidates"]');
    const pagerEl2 = dialog.querySelector('[data-slot="add-pager"]');
    const added = new Set();
    const search = { q: '', page: 1, seq: 0 };

    async function loadCandidates(page) {
      const my = ++search.seq;
      search.page = page;
      setHTML(listEl, loadingBlock());
      try {
        const res = await api.listPapers({ unassigned: true, q: search.q, page, limit: ADD_PAGE_SIZE });
        if (my !== search.seq) return;
        if (res.items.length === 0 && page > 1 && res.total_count > 0) { loadCandidates(res.total_pages); return; }
        if (!res.items.length) {
          setHTML(listEl, emptyBlock(search.q ? '검색 결과가 없어요' : '추가할 수 있는 논문이 없어요', '이미 다른 컬렉션에 들어 있는 논문은 보이지 않아요.'));
          setHTML(pagerEl2, '');
          return;
        }
        setHTML(listEl, html`<ul class="pick-list">${res.items.map((p) => html`<li class="pick-item">
          <span class="pick-main"><span class="pick-title">${paperTitle(p)}</span><span class="pick-sub">${[formatAuthors(p), formatPublished(p.published)].filter(Boolean).join(' · ') || '저자·출판 연도 정보 없음'}</span></span>
          ${added.has(p.paper_id)
            ? html`<button type="button" class="btn sm" disabled>추가됨</button>`
            : html`<button type="button" class="btn sm" data-add="${p.paper_id}">추가</button>`}
        </li>`)}</ul>`);
        setHTML(pagerEl2, pagination({ page: res.page, totalPages: res.total_pages, label: '추가할 논문 페이지', compact: true }));
      } catch (error) {
        if (my === search.seq) {
          setHTML(listEl, html`<p class="inline-error" role="alert">${errorMessage(error)}</p>`);
          setHTML(pagerEl2, '');
        }
      }
    }

    // 검색어를 바꾸면 1페이지부터 (요청서 1.5)
    dialog.querySelector('[data-slot="add-search"]').addEventListener('submit', (e) => {
      e.preventDefault();
      search.q = e.currentTarget.elements.q.value.trim();
      loadCandidates(1);
    });

    pagerEl2.addEventListener('click', (e) => {
      const pageBtn = e.target.closest('[data-page]');
      if (pageBtn && !pageBtn.disabled) loadCandidates(Number(pageBtn.dataset.page));
    });

    listEl.addEventListener('click', async (e) => {
      const button = e.target.closest('[data-add]');
      if (!button || added.has(button.dataset.add)) return;
      button.disabled = true;
      try {
        await api.addPaperToCollection(collectionId, button.dataset.add);
        added.add(button.dataset.add);
        changed = true;
        button.textContent = '추가됨';
      } catch (error) {
        button.disabled = false;
        // 다른 컬렉션 소속 충돌 등 (요청서 6.5): 자동으로 옮기지 않고 알린다.
        toast(errorMessage(error));
      }
    });

    // 추가한 논문은 최신순 맨 앞에 오므로 1페이지로 돌아간다.
    dialog.addEventListener('close', () => { if (changed) load(1); });
    loadCandidates(1);
  }

  view.addEventListener('click', (e) => {
    const pageBtn = e.target.closest('[data-page]');
    if (pageBtn && !pageBtn.disabled && view.contains(pageBtn)) { load(Number(pageBtn.dataset.page), { moveFocus: true }); return; }
    const el = e.target.closest('[data-action]');
    if (!el) return;
    switch (el.dataset.action) {
      case 'rename': rename(); break;
      case 'delete': el.closest('details')?.removeAttribute('open'); remove(); break;
      case 'add': openAdd(); break;
      case 'remove': removePaper(el); break;
      case 'reload-list': load(state.page); break;
      default: break;
    }
  });

  renderHeader();
  await load(state.page);
}
