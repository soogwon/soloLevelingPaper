// 3. 논문 상세 페이지 — GET /v1/papers/{id}, GET /v1/annotations, GET /v1/annotations/{id}, POST /v1/resume-requests
import { CONFIG } from '../config.js';
import { api } from '../api/index.js';
import { html, setHTML } from '../lib/html.js';
import { replaceQuery } from '../router.js';
import {
  icons, statusBadge, loadingBlock, emptyBlock, errorBlock, pagination, errorMessage,
  openDialog, toast, copyText, newRequestKey,
} from '../lib/ui.js';
import { pageFromQuery } from './papers.js';

// 상세 오른쪽 패널은 좁아서 메모·하이라이트를 한 페이지에 3개씩 보여준다.
const MEMO_PAGE_SIZE = 3;
import { compactAnnotationCard } from '../lib/annotation.js';
import {
  formatAuthorsYear, formatPosition, formatDate, formatDateTime, formatTime, paperTitle, SUMMARY_KINDS,
} from '../lib/format.js';

const TABS = [['', '전체'], ['note', '메모'], ['highlight', '하이라이트']];

function summarySection(summary) {
  if (!summary || !summary.items?.length) {
    return html`${emptyBlock('저장된 요약이 없어요', 'Claude에서 요약을 저장하면 여기에 보여요. 이 화면에서 새 요약을 만들지 않아요.')}`;
  }
  // 한 종류에 여러 문장이 올 수 있다 (요청서 3.2). 저장된 종류만 보여준다.
  const groups = SUMMARY_KINDS
    .map(([kind, label]) => ({ kind, label, items: summary.items.filter((i) => i.kind === kind) }))
    .filter((g) => g.items.length);

  return html`${groups.map((g) => {
    const evidence = g.items.flatMap((i) => i.evidence ?? []);
    const evidenceCount = new Set(g.items.flatMap((i) => i.evidence_ids ?? [])).size;
    const panelId = `ev-${g.kind}`;
    return html`<div class="sum-item">
      <div class="sum-item-head">
        <h3>${g.label}</h3>
        ${evidence.length
          ? html`<button type="button" class="link-btn" data-action="toggle-evidence" aria-expanded="false" aria-controls="${panelId}">근거 ${evidence.length}개 보기</button>`
          : evidenceCount ? html`<span class="hint">근거 ${evidenceCount}개 연결됨</span>` : ''}
      </div>
      <ul>${g.items.map((i) => html`<li>${i.text}</li>`)}</ul>
      ${evidence.length ? html`<ul class="evidence-list" id="${panelId}" hidden>${evidence.map((ev) => html`<li class="quote">
        <span class="badge b-src">${ev.target === 'translation' ? '한국어 번역' : '원문'} · ${formatPosition(ev.position) ?? '위치 정보 없음'}</span>
        <p class="quote-text">${ev.quote}</p>
      </li>`)}</ul>` : ''}
    </div>`;
  })}`;
}

function actionButton({ label, primary, available, message, action, iconHtml }) {
  return html`<div class="action">
    <button type="button" class="btn ${primary ? 'btn-primary' : ''}" data-action="${action}" ${available ? '' : html`disabled aria-describedby="${action}-why"`}>${label}${iconHtml ?? ''}</button>
    ${available ? '' : html`<span class="hint" id="${action}-why">${message}</span>`}
  </div>`;
}

export default async function paperDetailPage({ view, params, query }) {
  const paper = await api.getPaper(params.paperId, { learningRecordId: query.record, versionId: query.version });
  const title = paperTitle(paper);
  document.title = `${title} · Solo Leveling`;
  const versionId = paper.selected_version_id;
  const recordId = paper.selected_learning_record_id;

  setHTML(view, html`
    <nav class="breadcrumb" aria-label="현재 위치"><a href="#/papers">논문 아카이브</a><span aria-hidden="true">/</span><span>논문 상세</span></nav>

    <div class="detail-head">
      <div class="detail-head-main">
        ${statusBadge(paper.reading_status)}
        <h1 class="detail-title">${title}</h1>
        <p class="meta">${[formatAuthorsYear(paper), paper.file_name].filter(Boolean).join(' · ')}</p>
      </div>
      <div class="actions">
        ${actionButton({ label: '원문 보기', action: 'open-pdf', iconHtml: icons.external(), available: paper.file_access?.available, message: paper.file_access?.message ?? '원문을 열 수 없어요' })}
        ${actionButton({ label: 'Claude에서 이어서 학습', action: 'resume', primary: true, available: paper.resume?.available, message: paper.resume?.message ?? '이어하기를 할 수 없어요' })}
      </div>
    </div>

    <section class="card" aria-label="마지막 학습">
      <dl class="facts">
        <div class="fact"><dt>마지막 학습 위치</dt><dd>${formatPosition(paper.last_position) ?? '학습 위치가 저장되지 않았어요'}</dd></div>
        <div class="fact"><dt>마지막 학습</dt><dd>${paper.last_studied_at ? formatDateTime(paper.last_studied_at) : '기록 없음'}</dd></div>
        ${paper.version?.created_at ? html`<div class="fact"><dt>원문 버전</dt><dd class="normal">${formatDate(paper.version.created_at)} 등록본</dd></div>` : ''}
      </dl>
    </section>

    <div class="split">
      <section class="card panel panel-main" aria-labelledby="sum-h">
        <div class="panel-head">
          <h2 class="panel-title" id="sum-h">저장된 요약</h2>
          ${paper.summary ? html`<span class="note-tags">${paper.summary.author_type === 'user' ? html`<span class="badge b-gray">내 요약</span>` : html`<span class="badge b-ok">MCP 생성</span>`}<span class="hint">${formatDate(paper.summary.created_at)} 저장</span></span>` : ''}
        </div>
        ${summarySection(paper.summary)}
      </section>

      <section class="card panel panel-side" aria-labelledby="memo-h">
        <h2 class="panel-title" id="memo-h">메모·하이라이트 <small data-slot="memo-count"></small></h2>
        <div class="pills" role="group" aria-label="기록 종류">
          ${TABS.map(([v, l]) => html`<button type="button" class="pill sm" data-kind="${v}" aria-pressed="${String(v === '')}">${l}</button>`)}
        </div>
        <div data-slot="focus"></div>
        <div class="stack" data-slot="memos" tabindex="-1"></div>
        <div data-slot="memo-pager"></div>
      </section>
    </div>`);

  // ---------- 메모·하이라이트 (페이지 번호 방식) ----------
  const memoState = { kind: '', page: pageFromQuery(query.mpage), seq: 0, focus: null };
  const memoPanel = view.querySelector('.panel-side');
  const memosEl = view.querySelector('[data-slot="memos"]');
  const memoPagerEl = view.querySelector('[data-slot="memo-pager"]');
  const memoCountEl = view.querySelector('[data-slot="memo-count"]');
  const focusEl = view.querySelector('[data-slot="focus"]');

  function syncUrl() {
    replaceQuery({
      record: recordId,
      version: versionId,
      annotation: memoState.focus?.annotation_id ?? query.annotation,
      mpage: memoState.page > 1 ? memoState.page : '',
    });
  }

  async function loadMemos(page, { moveFocus = false } = {}) {
    const seq = ++memoState.seq;
    memoState.page = page;
    syncUrl();
    setHTML(memosEl, loadingBlock());
    try {
      const res = await api.listAnnotations({
        paper_id: paper.paper_id, version_id: versionId, kind: memoState.kind, sort: 'newest',
        page, limit: MEMO_PAGE_SIZE,
      });
      if (seq !== memoState.seq) return;
      if (res.items.length === 0 && page > 1 && res.total_count > 0) { loadMemos(res.total_pages, { moveFocus }); return; }

      // 위에 따로 강조해 둔 기록은 목록에서 한 번 더 보여주지 않는다.
      const focusId = memoState.focus?.annotation_id;
      const items = res.items.filter((a) => a.annotation_id !== focusId);
      if (res.items.length === 0) {
        setHTML(memosEl, emptyBlock(memoState.kind ? '이 종류의 기록이 없어요' : '아직 남긴 기록이 없어요', 'Claude에서 메모나 하이라이트를 저장하면 여기에 보여요.'));
      } else {
        setHTML(memosEl, html`${items.map((a) => compactAnnotationCard(a))}`);
      }
      memoCountEl.textContent = `${res.total_count}개`;
      setHTML(memoPagerEl, pagination({ page: res.page, totalPages: res.total_pages, totalCount: res.total_count, pageSize: res.page_size, label: '메모·하이라이트 페이지' }));
      if (moveFocus) {
        memoPanel.scrollIntoView({ block: 'start', behavior: 'smooth' });
        memosEl.focus({ preventScroll: true });
      }
    } catch (error) {
      if (seq !== memoState.seq) return;
      setHTML(memosEl, errorBlock(error, 'reload-memos'));
      setHTML(memoPagerEl, '');
    }
  }

  // 메모 페이지에서 넘어온 기록: 첫 목록에 없어도 개별 조회로 보여주고 강조한다 (요청서 3.4, 4.7).
  async function loadFocus() {
    if (!query.annotation) return;
    try {
      const a = await api.getAnnotation(query.annotation);
      if (a.paper?.paper_id !== paper.paper_id) return;
      memoState.focus = a;
      setHTML(focusEl, compactAnnotationCard(a, { focused: true }));
      const card = focusEl.querySelector('.note-card');
      card.scrollIntoView({ block: 'center', behavior: 'smooth' });
      card.focus({ preventScroll: true });
    } catch {
      setHTML(focusEl, html`<p class="inline-error">선택한 기록을 찾을 수 없어요.</p>`);
    }
  }

  // ---------- 버튼 ----------
  async function openPdf(button) {
    button.disabled = true;
    try {
      // 임시 URL이라 누를 때마다 새로 받는다. 저장해 두고 재사용하지 않는다 (요청서 9.4).
      const res = await api.getFileAccess(paper.paper_id, versionId);
      if (res.status === 'not_uploaded') {
        toast('이 원문 버전의 PDF가 서버에 업로드되지 않았어요.');
        return;
      }
      if (!res.url) {
        toast(CONFIG.USE_MOCK ? `예시 데이터에는 실제 파일이 없어요. (${res.file_name})` : '원문 주소를 받지 못했어요.');
        return;
      }
      window.open(res.url, '_blank', 'noopener');
    } catch (error) {
      toast(errorMessage(error));
    } finally {
      button.disabled = false;
    }
  }

  async function openResume(button) {
    button.disabled = true;
    let res;
    try {
      res = await api.createResumeRequest({
        paper_id: paper.paper_id,
        version_id: versionId,
        learning_record_id: recordId,
        annotation_id: memoState.focus?.annotation_id ?? undefined,
      }, newRequestKey());
    } catch (error) {
      toast(errorMessage(error));
      return;
    } finally {
      button.disabled = false;
    }
    const dialog = openDialog({
      title: 'Claude에서 이어서 학습하기',
      body: html`
        <p class="dialog-text">아래 문장을 복사해서 Solo Leveling MCP가 연결된 Claude 대화에 붙여넣으세요. 웹에서 Claude를 자동으로 열거나 이전 대화를 복원하지는 않아요.</p>
        <div class="codebox">
          <span class="hint">이어하기 코드 <strong class="code">${res.code}</strong></span>
          <p>${res.prompt_text}</p>
        </div>
        <div class="dialog-actions" style="justify-content:space-between">
          <span class="hint">${formatTime(res.expires_at)}까지 사용할 수 있어요</span>
          <span style="display:flex;gap:8px">
            <button type="button" class="btn" data-dialog-close>닫기</button>
            <button type="button" class="btn btn-primary" data-copy>문장 복사</button>
          </span>
        </div>
        <p class="hint">다른 PC라면 PDF와 검색 데이터를 다시 준비해야 할 수 있어요. 준비 상태는 Claude에서 알려줘요.</p>`,
    });
    const copyButton = dialog.querySelector('[data-copy]');
    copyButton.addEventListener('click', async () => {
      const ok = await copyText(res.prompt_text);
      copyButton.textContent = ok ? '복사됨' : '복사하지 못했어요';
    });
  }

  view.addEventListener('click', (e) => {
    const tab = e.target.closest('[data-kind]');
    if (tab) {
      memoState.kind = tab.dataset.kind;
      view.querySelectorAll('[data-kind]').forEach((b) => b.setAttribute('aria-pressed', String(b === tab)));
      loadMemos(1);
      return;
    }
    const pageBtn = e.target.closest('[data-page]');
    if (pageBtn && !pageBtn.disabled) { loadMemos(Number(pageBtn.dataset.page), { moveFocus: true }); return; }
    const el = e.target.closest('[data-action]');
    if (!el) return;
    switch (el.dataset.action) {
      case 'reload-memos': loadMemos(memoState.page); break;
      case 'open-pdf': openPdf(el); break;
      case 'resume': openResume(el); break;
      case 'toggle-evidence': {
        const panel = view.querySelector(`#${el.getAttribute('aria-controls')}`);
        const open = el.getAttribute('aria-expanded') !== 'true';
        el.setAttribute('aria-expanded', String(open));
        panel.hidden = !open;
        el.textContent = open ? '근거 접기' : `근거 ${panel.children.length}개 보기`;
        break;
      }
      default: break;
    }
  });

  // 강조할 기록을 먼저 확인한 뒤 목록을 불러와야 같은 기록이 두 번 보이지 않는다.
  // 목록에서 넘어온 기록 ID는 주소에 고정해 새로고침·뒤로가기 후에도 같은 기록과 페이지를 본다.
  await loadFocus();
  await loadMemos(memoState.page);
}
