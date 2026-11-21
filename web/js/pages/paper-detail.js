// 3. 논문 상세 페이지 — GET /v1/papers/{id}, GET /v1/annotations, GET /v1/annotations/{id}, POST /v1/resume-requests
import { hasFirstLearningSource } from '../lib/remote-first-learning.js';
import { buildHash } from '../router.js';
import { remotePaperJobs } from '../lib/remote-paper-jobs.js';
import { remotePaperDetail } from '../lib/remote-paper-detail.js';
import { CONFIG } from '../config.js';
import { api } from '../api/index.js';
import { html, setHTML } from '../lib/html.js';
import {
  icons, statusBadge, emptyBlock, errorMessage,
  openDialog, toast, copyText, newRequestKey,
} from '../lib/ui.js';
import {
  formatAuthorsYear, formatPosition, readingStatusUpdatedLabel, formatTime, paperTitle, SUMMARY_KINDS,
} from '../lib/format.js';

export function summarySection(summary) {
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
          : evidenceCount ? html`<span class="hint">근거 ID ${evidenceCount}개 저장됨 · 상세 인용은 아직 불러오지 않음</span>` : ''}
      </div>
      <ul>${g.items.map((i) => html`<li>${i.text}</li>`)}</ul>
      ${evidence.length ? html`<ul class="evidence-list" id="${panelId}" hidden>${evidence.map((ev) => html`<li class="quote">
        <span class="badge b-src">${ev.target === 'translation' ? '한국어 번역' : '원문'} · ${formatPosition(ev.position) ?? '위치 정보 없음'}</span>
        <p class="quote-text">${ev.quote}</p>
      </li>`)}</ul>` : ''}
    </div>`;
  })}`;
}

const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

export function remoteResumePrompt(paperId, versionId) {
  return `연결된 Solo Leveling 원격 MCP에서 논문 ID ${paperId}, PDF 판본 ID ${versionId}의 기존 학습 맥락을 open_remote_paper로 열어 이어서 학습해 줘. 새 학습 맥락은 만들지 말고, get_remote_learning으로 마지막 학습 위치를 확인해서 저장된 페이지가 있으면 그 페이지부터 read_remote_paper_page로 읽어 줘.`;
}

function actionButton({ label, primary, available, message, action, iconHtml }) {
  return html`<div class="action">
    <button type="button" class="btn ${primary ? 'btn-primary' : ''}" data-action="${action}" ${available ? '' : html`disabled aria-describedby="${action}-why"`}>${label}${iconHtml ?? ''}</button>
    ${available ? '' : html`<span class="hint" id="${action}-why">${message}</span>`}
  </div>`;
}

export default async function paperDetailPage({ view, params, query }) {
  let closed = false;
  const events = ['auth:logout', 'auth:expired', 'hashchange', 'pagehide'];
  const close = () => { closed = true; view.textContent = ''; events.forEach(e => window.removeEventListener(e, close)); };
  events.forEach(e => window.addEventListener(e, close));
  const paper = await api.getPaper(params.paperId, { learningRecordId: query.record, versionId: query.version });
  if (closed || !view.isConnected) return;
  const title = paperTitle(paper);
  document.title = `${title} · Solo Leveling`;
  const versionId = paper.selected_version_id;
  const recordId = paper.selected_learning_record_id;
  const starting = paper.reading_status === 'not_started';
  const canStart = !CONFIG.USE_MOCK && hasFirstLearningSource(paper);
  const startHref = buildHash(`/papers/${encodeURIComponent(paper.paper_id)}/start-learning`, {version:versionId});
  const remoteVersion = UUID.test(paper.reading_basis_version_id ?? '') ? paper.reading_basis_version_id : versionId;
  // 원격 학습은 로컬 학습 기록(이어하기 코드)이 없으므로 원격 MCP 요청 문장으로 이어간다.
  const remoteResume = !CONFIG.USE_MOCK && !paper.resume?.available
    && paper.reading_status_source === 'remote_learning'
    && UUID.test(paper.paper_id) && UUID.test(remoteVersion ?? '');
  const readingSource = {
    explicit: '연결 PC에서 저장한 상태',
    remote_learning: '원격 학습 기록',
    remote_file: '등록된 원격 원문',
    unknown: '상태 근거 미확인',
  }[paper.reading_status_source] ?? '상태 근거 미확인';

  setHTML(view, html`
    <nav class="breadcrumb" aria-label="현재 위치"><a href="#/papers">논문 아카이브</a><span aria-hidden="true">/</span><span>논문 상세</span></nav>

    <div class="detail-head">
      <div class="detail-head-main">
        ${statusBadge(paper.reading_status)}
        <span>상태 근거: ${readingSource}${paper.reading_basis_version_id ? ` · 기준 원문 버전: ${paper.reading_basis_version_id}` : ' · 기준 원문 버전 미확인'} · ${readingStatusUpdatedLabel(paper.reading_status_updated_at)} · 다른 판본의 학습 완료를 뜻하지 않습니다.</span>
        <h1 class="detail-title">${title}</h1>
        <p class="meta">${[formatAuthorsYear(paper), paper.file_name].filter(Boolean).join(' · ')}</p>
      </div>
      <div class="actions">
        ${actionButton({ label: '원문 보기', action: 'open-pdf', iconHtml: icons.external(), available: paper.file_access?.available, message: paper.file_access?.message ?? '원문을 열 수 없어요' })}
        ${starting ? (canStart
          ? html`<div class="action"><a class="btn btn-primary" href="${startHref}">Claude에서 학습 시작</a></div>`
          : actionButton({label:'Claude에서 학습 시작', action:'start-learning', primary:true,
            available:false, message:'서버에서 검증을 마친 PDF가 필요해요.'}))
          : actionButton({ label: 'Claude에서 이어서 학습', action: remoteResume ? 'remote-resume' : 'resume', available: remoteResume || paper.resume?.available, primary: true, message: paper.resume?.message ?? '이어하기를 할 수 없어요' })}
      </div>
    </div>

    <section class="card" aria-label="마지막 학습" data-slot="remote-progress"></section>

    <div class="split">
      <section class="card panel panel-main" data-slot="remote-summary"></section>
      <section class="card panel panel-side" data-slot="remote-notes"></section>
    </div>
    ${CONFIG.USE_MOCK ? '' : html`
      <details class="card panel processing-jobs">
        <summary class="panel-title">처리 작업</summary>
        <div class="processing-jobs-body" data-slot="remote-jobs"></div>
      </details>`}
  `);

  const detailViews = {
    progress: view.querySelector('[data-slot="remote-progress"]'),
    summary: view.querySelector('[data-slot="remote-summary"]'),
    notes: view.querySelector('[data-slot="remote-notes"]'),
  };
  if (CONFIG.USE_MOCK) {
    setHTML(detailViews.progress, emptyBlock('예시 모드에서는 원격 학습 기록을 제공하지 않아요', '실제 계정으로 로그인하면 원격 학습 위치와 기록이 여기에 보여요.'));
    setHTML(detailViews.summary, html`<h2 class="panel-title">요약</h2>${emptyBlock('예시 모드에서는 원격 요약을 제공하지 않아요')}`);
    setHTML(detailViews.notes, html`<h2 class="panel-title">메모·하이라이트 <small>0개</small></h2>${emptyBlock('예시 모드에서는 원격 기록을 제공하지 않아요')}`);
  }
  const detailReady = CONFIG.USE_MOCK ? Promise.resolve()
    : remotePaperDetail(detailViews, paper.paper_id, versionId);
  const jobs = view.querySelector('.processing-jobs');
  if (jobs) {
    const loadJobs = () => {
      if (closed || !jobs.open || !jobs.isConnected) return;
      jobs.removeEventListener('toggle', loadJobs);
      void remotePaperJobs(jobs.querySelector('[data-slot="remote-jobs"]'), paper.paper_id, versionId);
    };
    jobs.addEventListener('toggle', loadJobs);
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
      if (res.status === 'pending') {
        toast('PDF 업로드 확인이 진행 중이에요. 잠시 뒤 다시 시도해 주세요.');
        return;
      }
      if (res.status === 'failed') {
        toast('PDF 업로드 확인에 실패했어요. 연결된 PC에서 다시 업로드해 주세요.');
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

  function openRemoteResume() {
    const prompt = remoteResumePrompt(paper.paper_id, remoteVersion);
    const dialog = openDialog({
      title: 'Claude에서 이어서 학습하기',
      body: html`
        <p class="dialog-text">아래 요청을 복사해서 Solo Leveling 원격 MCP가 연결된 Claude 대화에 붙여넣으세요. 이 판본의 기존 학습 맥락을 열고, 저장된 위치가 있으면 그 페이지부터 이어가요.</p>
        <div class="codebox"><p>${prompt}</p></div>
        <div class="dialog-actions">
          <button type="button" class="btn" data-dialog-close>닫기</button>
          <button type="button" class="btn btn-primary" data-copy>요청 복사</button>
        </div>
        <p class="hint">학습 위치는 Claude에게 "지금 페이지 위치 저장해 줘"라고 요청하면 저장돼요.</p>`,
    });
    const copyButton = dialog.querySelector('[data-copy]');
    copyButton.addEventListener('click', async () => {
      const ok = await copyText(prompt);
      copyButton.textContent = ok ? '복사됨' : '복사하지 못했어요';
    });
  }

  view.addEventListener('click', (e) => {
    const el = e.target.closest('[data-action]');
    if (!el) return;
    switch (el.dataset.action) {
      case 'open-pdf': openPdf(el); break;
      case 'resume': openResume(el); break;
      case 'remote-resume': openRemoteResume(); break;
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

  await detailReady;
}
