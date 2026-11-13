// 메모·하이라이트 카드 (논문 상세, 메모 페이지, 검색 결과에서 같이 쓴다)
import { html } from './html.js';
import { buildHash } from '../router.js';
import { icons, kindBadge, authorBadge } from './ui.js';
import { formatPosition, formatDateTime, formatTime } from './format.js';

const TARGET_LABEL = { original: '원문', translation: '한국어 번역' };

function firstPosition(annotation) {
  for (const s of annotation.segments ?? []) {
    const text = formatPosition(s.position);
    if (text) return text;
    if (s.chunk_id && Number.isInteger(s.start_offset) && Number.isInteger(s.end_offset)) {
      return `청크 ${s.chunk_id} · 문자 구간 [${s.start_offset}, ${s.end_offset})`;
    }
  }
  return null;
}

export function annotationDetailHref(a) {
  return buildHash(`/papers/${encodeURIComponent(a.paper.paper_id)}`, {
    version: a.version_id,
    record: a.learning_record_id,
    annotation: a.annotation_id,
  });
}

function quotes(a) {
  return (a.segments ?? []).map((s) => html`<div class="quote">
    <span class="badge b-src">${TARGET_LABEL[s.target] ?? '인용'}</span>
    <p class="quote-text">${s.quote}</p>
  </div>`);
}

function dateText(a, withDate) {
  if (!a.created_at) return '작성일 미상';
  const base = withDate ? formatDateTime(a.created_at) : formatTime(a.created_at);
  const edited = a.updated_at && a.updated_at !== a.created_at ? ' · 수정됨' : '';
  return `작성 ${base}${edited}`;
}

/** 논문 상세 오른쪽 패널용 작은 카드 */
export function compactAnnotationCard(a, { focused = false } = {}) {
  return html`<article class="note-card ${focused ? 'is-focus' : ''}" data-annotation-id="${a.annotation_id}" ${focused ? html`tabindex="-1"` : ''}>
    ${focused ? html`<span class="focus-label">메모 페이지에서 선택한 기록</span>` : ''}
    <div class="note-top">
      <span class="note-tags">${kindBadge(a.kind)}${authorBadge(a.author_type)}</span>
      <span class="note-pos">${firstPosition(a) ?? '위치 연결 없음'}</span>
    </div>
    ${quotes(a)}
    ${a.body ? html`<p class="note-body">${a.body}</p>` : ''}
    <span class="note-date">${dateText(a, true)}</span>
  </article>`;
}

/** 메모 페이지·검색 결과용 큰 카드 */
export function fullAnnotationCard(a, { showDate = false } = {}) {
  return html`<article class="note-card lg">
    <div class="note-top">
      <span class="note-tags">${kindBadge(a.kind)}${authorBadge(a.author_type)}</span>
      <span class="note-date">${dateText(a, showDate)}</span>
    </div>
    ${quotes(a)}
    ${a.body ? html`<p class="note-body">${a.body}</p>` : ''}
    <div class="note-foot">
      <div class="note-paper">
        ${icons.file(18)}
        <div>
          <div class="note-paper-title">${a.paper.title}</div>
          <div class="note-paper-pos">${firstPosition(a) ?? '위치 연결 없음'}</div>
        </div>
      </div>
      <a class="btn sm" href="${annotationDetailHref(a)}">논문 상세 보기 ${icons.chevron()}</a>
    </div>
  </article>`;
}
