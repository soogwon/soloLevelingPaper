import { html, raw, setHTML } from './html.js';
import { readingStatusLabel, normalizeStatus } from './format.js';

// ---------- 아이콘 (장식용: aria-hidden) ----------
const svg = (body, size = 20, width = 1.8) => raw(
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`,
);

export const icons = {
  file: (s) => svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>', s),
  folder: (s) => svg('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>', s),
  plus: (s = 16) => svg('<path d="M12 5v14"/><path d="M5 12h14"/>', s, 2.2),
  external: (s = 16) => svg('<path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>', s, 2),
  chevron: (s = 15) => svg('<path d="m9 6 6 6-6 6"/>', s, 2),
  chevronLeft: (s = 16) => svg('<path d="m15 6-6 6 6 6"/>', s, 2),
  calendar: (s = 17) => svg('<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17"/><path d="M8 3v4"/><path d="M16 3v4"/>', s),
  reset: (s = 16) => svg('<path d="M4 12a8 8 0 1 0 2.3-5.6"/><path d="M4 4v4h4"/>', s, 2),
  search: (s = 18) => svg('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>', s, 2),
  more: (s = 20) => raw(`<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>`),
  laptop: (s = 24) => svg('<rect x="4" y="5" width="16" height="11" rx="1.5"/><path d="M2 19h20"/>', s, 1.6),
};

// ---------- 배지 ----------
export function statusBadge(status) {
  const s = normalizeStatus(status);
  return html`<span class="badge b-${s}">${readingStatusLabel(s)}</span>`;
}

export function kindBadge(kind) {
  return kind === 'highlight'
    ? html`<span class="badge b-highlight">하이라이트</span>`
    : html`<span class="badge b-note">메모</span>`;
}

/** 작성 주체. 값이 없으면 추측하지 않고 표시하지 않는다 (요청서 3.3). */
export function authorBadge(authorType) {
  if (authorType === 'user') return html`<span class="badge b-gray">내 기록</span>`;
  if (authorType === 'ai' || authorType === 'mcp') return html`<span class="badge b-ok">AI 생성</span>`;
  return '';
}

// ---------- 상태 화면 ----------
export function loadingBlock(label = '불러오는 중이에요') {
  return html`<div class="state" role="status"><span class="spinner" aria-hidden="true"></span><p class="state-desc">${label}</p></div>`;
}

export function emptyBlock(title, desc = '') {
  return html`<div class="state"><p class="state-title">${title}</p>${desc ? html`<p class="state-desc">${desc}</p>` : ''}</div>`;
}

export function errorMessage(error) {
  if (error?.status === 0) return '서버에 연결할 수 없어요. 네트워크를 확인해 주세요.';
  if (error?.status === 404) return '기록을 찾을 수 없어요.';
  return error?.message || '조회하지 못했어요. 잠시 후 다시 시도해 주세요.';
}

/** 조회 실패. 빈 목록과 구분해서 보여준다 (요청서 1.6). action은 재시도 버튼의 data-action 값. */
export function errorBlock(error, action = 'reload-route') {
  const requestId = error?.requestId ? html`<p class="hint">문의 번호 ${error.requestId}</p>` : '';
  return html`<div class="state" role="alert"><p class="state-title">불러오지 못했어요</p><p class="state-desc">${errorMessage(error)}</p>${requestId}<button type="button" class="btn sm" data-action="${action}">다시 시도</button></div>`;
}

// ---------- 페이지 번호 ----------
/** 보여줄 번호: 처음·끝·현재 주변만, 사이는 … (예: 1 … 4 5 6 … 12) */
function pageNumbers(page, total) {
  const set = new Set([1, total, page - 1, page, page + 1]);
  if (page <= 3) [2, 3, 4].forEach((n) => set.add(n));
  if (page >= total - 2) [total - 1, total - 2, total - 3].forEach((n) => set.add(n));
  const nums = [...set].filter((n) => n >= 1 && n <= total).sort((a, b) => a - b);
  const out = [];
  nums.forEach((n, i) => {
    if (i > 0 && n - nums[i - 1] > 1) out.push(null);
    out.push(n);
  });
  return out;
}

/**
 * 페이지 이동 버튼. 버튼마다 data-page가 있어서 화면에서 클릭을 받아 처리한다.
 * @param {{ page: number, totalPages: number, totalCount?: number, pageSize?: number, label?: string, compact?: boolean }} o
 */
export function pagination({ page, totalPages, totalCount, pageSize, label = '페이지', compact = false }) {
  if (!totalPages || totalPages <= 1) return '';
  const range = totalCount && pageSize
    ? html`<span class="pager-range">${totalCount}개 중 ${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, totalCount)}</span>`
    : '';
  const numbers = compact
    ? html`<span class="pager-status" aria-live="polite">${page} / ${totalPages}</span>`
    : pageNumbers(page, totalPages).map((n) => (n === null
      ? html`<span class="pager-gap" aria-hidden="true">…</span>`
      : html`<button type="button" class="pager-btn" data-page="${n}" aria-label="${n}페이지" ${n === page ? raw('aria-current="page"') : ''}>${n}</button>`));
  return html`<nav class="pager ${compact ? 'compact' : ''}" aria-label="${label}">
    <button type="button" class="pager-btn" data-page="${page - 1}" aria-label="이전 페이지" ${page <= 1 ? raw('disabled') : ''}>${icons.chevronLeft()}</button>
    ${numbers}
    <button type="button" class="pager-btn" data-page="${page + 1}" aria-label="다음 페이지" ${page >= totalPages ? raw('disabled') : ''}>${icons.chevron(16)}</button>
    ${range}
  </nav>`;
}

// ---------- 대화상자 ----------
let dialogSeq = 0;

/** 네이티브 <dialog>를 연다. 닫히면 DOM에서 제거된다. */
export function openDialog({ title, body, size = '', role = 'dialog' }) {
  const id = `dlg-title-${++dialogSeq}`;
  const dialog = document.createElement('dialog');
  dialog.className = `dialog ${size}`;
  dialog.setAttribute('aria-labelledby', id);
  if (role !== 'dialog') dialog.setAttribute('role', role);
  setHTML(dialog, html`<div class="dialog-inner"><h2 class="dialog-title" id="${id}">${title}</h2>${body}</div>`);
  document.body.append(dialog);
  dialog.addEventListener('close', () => dialog.remove());
  dialog.addEventListener('click', (e) => {
    if (e.target === dialog) dialog.close();
    if (e.target.closest('[data-dialog-close]')) dialog.close();
  });
  dialog.showModal();
  return dialog;
}

export function confirmDialog({ title, message, confirmLabel = '확인', danger = false }) {
  return new Promise((resolve) => {
    let result = false;
    const dialog = openDialog({
      title,
      role: 'alertdialog',
      body: html`<p class="dialog-text">${message}</p>
        <div class="dialog-actions">
          <button type="button" class="btn" data-dialog-close>취소</button>
          <button type="button" class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-confirm>${confirmLabel}</button>
        </div>`,
    });
    dialog.querySelector('[data-confirm]').addEventListener('click', () => { result = true; dialog.close(); });
    dialog.addEventListener('close', () => resolve(result));
  });
}

// ---------- 알림 ----------
export function toast(message) {
  const region = document.querySelector('.toast-region');
  if (!region) return;
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = message;
  region.append(el);
  setTimeout(() => el.remove(), 3200);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.append(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  }
}

export function newRequestKey() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
