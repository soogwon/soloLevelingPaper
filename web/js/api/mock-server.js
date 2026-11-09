// 요청서의 규칙대로 동작하는 가짜 서버. 화면 개발과 시연 전 확인용이다.
// 실제 서버 응답 형태가 정해지면 이 파일도 그 형태에 맞춰 두면 화면 코드를 바꾸지 않아도 된다.
import { CONFIG } from '../config.js';
import { ApiError } from './client.js';
import { createMockDb } from './mock-data.js';
import { dateKey, isDateKey } from '../lib/format.js';

const db = createMockDb();
const LOGGED_OUT_KEY = 'slp-mock-logged-out';
const STATUSES = ['not_started', 'reading', 'completed', 'unknown'];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const nowIso = () => new Date().toISOString();
const randomId = (prefix) => `${prefix}_${Math.random().toString(36).slice(2, 10)}`;

function fail(status, code, message, field = null) {
  return new ApiError({ status, code, message, field, requestId: randomId('req') });
}
const notFound = () => fail(404, 'not_found', '기록을 찾을 수 없어요.');

function isLoggedIn() {
  try { return sessionStorage.getItem(LOGGED_OUT_KEY) !== '1'; } catch { return true; }
}
function setLoggedIn(value) {
  try { value ? sessionStorage.removeItem(LOGGED_OUT_KEY) : sessionStorage.setItem(LOGGED_OUT_KEY, '1'); } catch { /* 저장소 없음 */ }
}

// ---------- 목록 공통: 페이지 번호 방식 ----------
// 요청 page(1부터), limit(기본 20, 최대 100) → { items, total_count, page, page_size, total_pages }
// 범위를 넘는 page는 빈 items를 돌려주고, 프론트가 마지막 페이지로 다시 요청한다.
function paginate(list, query) {
  const limit = Number(query.limit ?? CONFIG.PAGE_SIZE);
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw fail(400, 'invalid_argument', '조회 개수는 1~100이어야 해요.', 'limit');
  const page = Number(query.page ?? 1);
  if (!Number.isInteger(page) || page < 1) throw fail(400, 'invalid_argument', '페이지 번호가 올바르지 않아요.', 'page');
  const totalPages = Math.max(1, Math.ceil(list.length / limit));
  return {
    items: list.slice((page - 1) * limit, page * limit),
    total_count: list.length,
    page,
    page_size: limit,
    total_pages: totalPages,
  };
}

// ---------- 논문 ----------
const paperTitleOf = (p) => p.title ?? p.version.file_name;

function toPaperListItem(p) {
  return {
    paper_id: p.paper_id,
    title: p.title,
    file_name: p.version.file_name,
    authors: p.authors,
    published: p.published,
    selected_learning_record_id: p.record?.learning_record_id ?? null,
    selected_version_id: p.version.version_id,
    last_position: p.record?.last_position ?? null,
    last_studied_at: p.record?.last_studied_at ?? null,
    reading_status: p.reading_status,
    collection_id: db.membership.get(p.paper_id)?.collection_id ?? null,
  };
}

function byRecentStudy(a, b) {
  const ta = a.record?.last_studied_at ?? '';
  const tb = b.record?.last_studied_at ?? '';
  if (ta !== tb) return ta < tb ? 1 : -1; // 최근 학습순, 학습일 없는 논문은 뒤로
  return a.paper_id < b.paper_id ? -1 : 1;
}

function listPapers(query) {
  let list = db.papers.slice();
  const q = (query.q ?? '').trim().toLowerCase();
  if (q) list = list.filter((p) => paperTitleOf(p).toLowerCase().includes(q));
  if (query.reading_status) {
    if (!STATUSES.includes(query.reading_status)) throw fail(400, 'invalid_argument', '읽기 상태 값이 올바르지 않아요.', 'reading_status');
    list = list.filter((p) => p.reading_status === query.reading_status);
  }
  if (query.collection_id) list = list.filter((p) => db.membership.get(p.paper_id)?.collection_id === query.collection_id);
  if (query.unassigned === 'true') list = list.filter((p) => !db.membership.has(p.paper_id));
  list.sort(byRecentStudy);
  return paginate(list.map(toPaperListItem), query);
}

function getPaper(paperId, query) {
  const p = db.papers.find((x) => x.paper_id === paperId);
  if (!p) throw notFound();
  if (query.learning_record_id && query.learning_record_id !== p.record?.learning_record_id) throw notFound();
  if (query.version_id && query.version_id !== p.version.version_id) throw fail(400, 'version_mismatch', '학습 기록과 원문 버전이 맞지 않아요.', 'version_id');
  const uploaded = p.version.pdf_status === 'uploaded';
  return {
    ...toPaperListItem(p),
    version: { version_id: p.version.version_id, file_name: p.version.file_name, created_at: p.version.created_at },
    summary: p.summary ? { ...p.summary, learning_record_id: p.record?.learning_record_id, version_id: p.version.version_id } : null,
    file_access: uploaded
      ? { available: true, reason: null, message: null }
      : { available: false, reason: 'pdf_not_uploaded', message: 'PDF가 업로드되지 않았어요' },
    resume: p.record
      ? { available: true, reason: null, message: null }
      : { available: false, reason: 'no_learning_record', message: '저장된 학습 기록이 없어요' },
  };
}

function getFileAccess(paperId, versionId) {
  const p = db.papers.find((x) => x.paper_id === paperId && x.version.version_id === versionId);
  if (!p) throw notFound();
  // 서버에 PDF가 없으면 주소 없이 '업로드되지 않음' 상태를 돌려준다 (요청서 9.4).
  if (p.version.pdf_status !== 'uploaded') {
    return { status: 'not_uploaded', url: null, expires_at: null, file_name: p.version.file_name, file_size: null, content_type: null, version_id: versionId };
  }
  // 예시 데이터에는 실제 파일이 없어서 url이 비어 있다.
  return {
    status: 'available',
    url: null,
    expires_at: new Date(Date.now() + 5 * 60000).toISOString(),
    file_name: p.version.file_name,
    file_size: 2_412_544,
    content_type: 'application/pdf',
    version_id: versionId,
  };
}

// ---------- 메모·하이라이트 (요청서 3.3, 4) ----------
function listAnnotations(query) {
  let list = db.annotations.slice();
  if (query.paper_id) list = list.filter((a) => a.paper.paper_id === query.paper_id);
  if (query.version_id) list = list.filter((a) => a.version_id === query.version_id);
  if (query.kind) {
    if (!['note', 'highlight'].includes(query.kind)) throw fail(400, 'invalid_argument', '기록 종류 값이 올바르지 않아요.', 'kind');
    list = list.filter((a) => a.kind === query.kind);
  }
  const { from, to } = query;
  if (from !== undefined || to !== undefined) {
    if (from !== undefined && !isDateKey(from)) throw fail(400, 'invalid_argument', '시작일 형식이 올바르지 않아요.', 'from');
    if (to !== undefined && !isDateKey(to)) throw fail(400, 'invalid_argument', '종료일 형식이 올바르지 않아요.', 'to');
    if (from && to && from > to) throw fail(400, 'invalid_argument', '시작일이 종료일보다 늦어요.', 'from');
    // 작성일 미상 기록은 날짜 조건이 있으면 제외한다. 업로드 날짜로 끼워 넣지 않는다.
    list = list.filter((a) => {
      const key = dateKey(a.created_at);
      return key && (!from || key >= from) && (!to || key <= to);
    });
  }
  const q = (query.q ?? '').trim().toLowerCase();
  if (q) {
    list = list.filter((a) => a.body.toLowerCase().includes(q)
      || a.paper.title.toLowerCase().includes(q)
      || a.segments.some((s) => s.quote.toLowerCase().includes(q)));
  }
  const oldest = query.sort === 'oldest';
  list.sort((a, b) => {
    const ta = a.created_at ?? '';
    const tb = b.created_at ?? '';
    if (!ta !== !tb) return ta ? -1 : 1; // 작성일 미상은 항상 뒤
    if (ta !== tb) return (ta < tb ? -1 : 1) * (oldest ? 1 : -1);
    return a.annotation_id < b.annotation_id ? -1 : 1;
  });
  const dateCounts = {};
  for (const a of list) {
    const key = dateKey(a.created_at) ?? 'unknown';
    dateCounts[key] = (dateCounts[key] ?? 0) + 1;
  }
  return { ...paginate(list, query), date_counts: dateCounts };
}

// ---------- 컬렉션 (요청서 5, 6) ----------
const normalizeName = (name) => name.trim().normalize('NFKC').toLowerCase();

function validateName(value, exceptId = null) {
  const name = typeof value === 'string' ? value.trim() : '';
  if (!name) throw fail(400, 'invalid_argument', '컬렉션 이름을 입력해 주세요.', 'name');
  if ([...name].length > 100) throw fail(400, 'invalid_argument', '이름은 100자 이하로 입력해 주세요.', 'name');
  const dup = db.collections.some((c) => c.collection_id !== exceptId && normalizeName(c.name) === normalizeName(name));
  if (dup) throw fail(409, 'collection_name_conflict', '같은 이름의 컬렉션이 이미 있어요.', 'name');
  return name;
}

const countIn = (collectionId) => [...db.membership.values()].filter((m) => m.collection_id === collectionId).length;
const withCount = (c) => ({ ...c, paper_count: countIn(c.collection_id) });
const findCollection = (collectionId) => {
  const c = db.collections.find((x) => x.collection_id === collectionId);
  if (!c) throw notFound();
  return c;
};

function listCollectionPapers(collectionId, query) {
  findCollection(collectionId);
  const list = db.papers
    .filter((p) => db.membership.get(p.paper_id)?.collection_id === collectionId)
    .map((p) => ({
      paper_id: p.paper_id, title: p.title, file_name: p.version.file_name, authors: p.authors, published: p.published,
      added_at: db.membership.get(p.paper_id).added_at,
    }))
    .sort((a, b) => (a.added_at < b.added_at ? 1 : a.added_at > b.added_at ? -1 : 0));
  return paginate(list, query);
}

function addPaper(collectionId, paperId) {
  const c = findCollection(collectionId);
  if (!db.papers.some((p) => p.paper_id === paperId)) throw notFound();
  const current = db.membership.get(paperId);
  if (current && current.collection_id !== collectionId) {
    throw fail(409, 'collection_membership_conflict', '이미 다른 컬렉션에 들어 있는 논문이에요.');
  }
  if (!current) {
    db.membership.set(paperId, { collection_id: collectionId, added_at: nowIso() });
    c.updated_at = nowIso();
  }
  return { collection_id: collectionId, paper_id: paperId, paper_count: countIn(collectionId) };
}

function removePaper(collectionId, paperId) {
  const c = findCollection(collectionId);
  if (db.membership.get(paperId)?.collection_id === collectionId) {
    db.membership.delete(paperId);
    c.updated_at = nowIso();
  }
  return { collection_id: collectionId, paper_id: paperId, paper_count: countIn(collectionId) };
}

// ---------- 이어하기 (요청서 10) ----------
function createResume(body) {
  const p = db.papers.find((x) => x.paper_id === body?.paper_id);
  if (!p) throw notFound();
  if (!p.record || (body.learning_record_id && body.learning_record_id !== p.record.learning_record_id)) {
    throw fail(409, 'no_learning_record', '저장된 학습 기록이 없어 이어하기를 만들 수 없어요.');
  }
  const code = `SLP-${Math.random().toString(36).slice(2, 6).toUpperCase()}-${Math.random().toString(36).slice(2, 6).toUpperCase()}`;
  return {
    resume_request_id: randomId('rsm'),
    code,
    expires_at: new Date(Date.now() + 10 * 60000).toISOString(),
    prompt_text: `Solo Leveling에 저장된 학습 기록을 이어서 공부할게. 이어하기 코드: ${code}`,
    status: 'ready',
    reason: null,
  };
}

// ---------- 라우팅 ----------
function withIdempotency(key, fn) {
  if (!key) return fn();
  if (db.idempotency.has(key)) return db.idempotency.get(key);
  const result = fn();
  db.idempotency.set(key, result);
  return result;
}

export async function mockRequest(method, path, { query = {}, body, idempotencyKey } = {}) {
  await sleep(CONFIG.MOCK_LATENCY_MS);
  const clean = Object.fromEntries(Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== ''));
  const seg = path.split('/').filter(Boolean).map(decodeURIComponent);
  const route = `${method} /${seg.map((s, i) => (i % 2 === 1 && !['papers', 'versions', 'file-access'].includes(s) ? ':id' : s)).join('/')}`;

  if (method === 'POST' && path === '/auth/mock-login') { setLoggedIn(true); return null; }
  if (method === 'POST' && path === '/auth/logout') { setLoggedIn(false); return null; }
  if (!isLoggedIn()) throw fail(401, 'unauthenticated', '로그인이 필요해요.');

  switch (route) {
    case 'GET /me': return { ...db.me };
    case 'GET /devices': return { items: db.devices.map((d) => ({ ...d })) };
    case 'DELETE /devices/:id': {
      const i = db.devices.findIndex((d) => d.device_id === seg[1]);
      if (i < 0) throw notFound();
      db.devices.splice(i, 1);
      return null;
    }
    case 'GET /papers': return listPapers(clean);
    case 'GET /papers/:id': return getPaper(seg[1], clean);
    case 'GET /papers/:id/versions/:id/file-access': return getFileAccess(seg[1], seg[3]);
    case 'GET /annotations': return listAnnotations(clean);
    case 'GET /annotations/:id': {
      const a = db.annotations.find((x) => x.annotation_id === seg[1]);
      if (!a) throw notFound();
      return { ...a };
    }
    case 'GET /collections': {
      const list = db.collections.slice().sort((a, b) => (a.created_at < b.created_at ? 1 : -1)).map(withCount);
      return paginate(list, clean);
    }
    case 'POST /collections':
      return withIdempotency(idempotencyKey, () => {
        const name = validateName(body?.name);
        const c = { collection_id: randomId('col'), name, created_at: nowIso(), updated_at: nowIso() };
        db.collections.push(c);
        return withCount(c);
      });
    case 'GET /collections/:id': return withCount(findCollection(seg[1]));
    case 'PATCH /collections/:id': {
      const c = findCollection(seg[1]);
      c.name = validateName(body?.name, c.collection_id);
      c.updated_at = nowIso();
      return withCount(c);
    }
    case 'DELETE /collections/:id': {
      findCollection(seg[1]);
      for (const [paperId, m] of db.membership) if (m.collection_id === seg[1]) db.membership.delete(paperId);
      db.collections = db.collections.filter((c) => c.collection_id !== seg[1]);
      return null;
    }
    case 'GET /collections/:id/papers': return listCollectionPapers(seg[1], clean);
    case 'PUT /collections/:id/papers/:id': return addPaper(seg[1], seg[3]);
    case 'DELETE /collections/:id/papers/:id': return removePaper(seg[1], seg[3]);
    case 'POST /resume-requests': return withIdempotency(idempotencyKey, () => createResume(body));
    default: throw notFound();
  }
}
