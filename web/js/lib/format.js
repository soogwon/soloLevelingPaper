import { CONFIG } from '../config.js';

// ---------- 날짜 (요청서 1.3: 한국 시간 기준) ----------
const partsFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: CONFIG.TIME_ZONE,
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});

function parts(value) {
  if (value === null || value === undefined || value === '') return null;
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const out = {};
  for (const p of partsFormatter.formatToParts(date)) out[p.type] = p.value;
  return out;
}

/** ISO 시각 → 한국 날짜 키 'YYYY-MM-DD' */
export function dateKey(value) {
  const p = parts(value);
  return p ? `${p.year}-${p.month}-${p.day}` : null;
}

export function formatDate(value) {
  const p = parts(value);
  return p ? `${p.year}.${p.month}.${p.day}` : '';
}

export function formatDateTime(value) {
  const p = parts(value);
  return p ? `${p.year}.${p.month}.${p.day} ${p.hour}:${p.minute}` : '';
}

export function formatTime(value) {
  const p = parts(value);
  return p ? `${p.hour}:${p.minute}` : '';
}

export function todayKey() {
  return dateKey(new Date());
}

/** 'YYYY-MM-DD'에 days를 더한 날짜 키 */
export function shiftKey(key, days) {
  const [y, m, d] = key.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

export function isDateKey(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value + 'T00:00:00Z'));
}

const headingFormatter = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'UTC', year: 'numeric', month: 'long', day: 'numeric', weekday: 'long',
});

/** 'YYYY-MM-DD' → '2026년 10월 31일 토요일' */
export function formatDayHeading(key) {
  const [y, m, d] = key.split('-').map(Number);
  return headingFormatter.format(new Date(Date.UTC(y, m - 1, d)));
}

export function formatKey(key) {
  return key ? key.replaceAll('-', '.') : '';
}

// ---------- 논문 정보 ----------
const STATUS_LABELS = {
  not_started: '읽기 전',
  reading: '읽는 중',
  completed: '읽기 완료',
  unknown: '상태 미확인',
};

export function readingStatusLabel(status) {
  return STATUS_LABELS[status] ?? STATUS_LABELS.unknown;
}

export function normalizeStatus(status) {
  return status in STATUS_LABELS ? status : 'unknown';
}

/** 저자 목록 → 'Vaswani 외'. author_display가 오면 그것을 우선한다. */
export function formatAuthors(paper) {
  if (paper.author_display) return paper.author_display;
  const authors = paper.authors;
  if (!Array.isArray(authors) || authors.length === 0) return null;
  const first = String(authors[0]).trim();
  if (authors.length === 1) return first;
  const last = first.split(/\s+/).pop();
  return `${last} 외`;
}

/** 확보된 정밀도만큼만 표시한다. 연도만 있으면 연도만 (요청서 1.3, 6.3). */
export function formatPublished(published) {
  if (!published || !published.year) return null;
  const pad = (n) => String(n).padStart(2, '0');
  if (published.precision === 'day' && published.month && published.day) return `${published.year}.${pad(published.month)}.${pad(published.day)}`;
  if (published.precision === 'month' && published.month) return `${published.year}.${pad(published.month)}`;
  return String(published.year);
}

export function formatAuthorsYear(paper) {
  const items = [formatAuthors(paper), formatPublished(paper.published)].filter(Boolean);
  return items.length ? items.join(' · ') : null;
}

export function paperTitle(paper) {
  return paper.title || paper.file_name || '제목 없음';
}

/** 위치 정보 → 'PDF 5페이지 · 3.2절 Multi-Head Attention'. 저장되지 않은 값은 만들지 않는다 (요청서 1.4). */
export function formatPosition(position) {
  if (!position) return null;
  const out = [];
  if (position.pdf_page !== null && position.pdf_page !== undefined) out.push(`PDF ${position.pdf_page}페이지`);
  const section = [position.section_number ? `${position.section_number}절` : null, position.section_title].filter(Boolean).join(' ');
  if (section) out.push(section);
  return out.length ? out.join(' · ') : null;
}

export const SUMMARY_KINDS = [
  ['claim', '주장'],
  ['method', '방법'],
  ['result', '결과'],
  ['limitation', '한계'],
];
