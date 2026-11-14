// 화면 코드는 이 파일의 api 객체만 사용한다.
// 기본은 실제 백엔드이며 ?mock=1일 때만 메모리 예시 서버를 사용한다.
import { CONFIG } from '../config.js';
import { request, ApiError, RouteNotFoundError } from './client.js';

async function send(method, path, options) {
  const fn = CONFIG.USE_MOCK ? (await import('./mock-server.js')).mockRequest : request;
  try {
    return await fn(method, path, options);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      window.dispatchEvent(new CustomEvent('auth:expired'));
    }
    throw error;
  }
}

const id = (value) => encodeURIComponent(value);

// 목록은 페이지 번호 방식: page(1부터), limit
const page = (params = {}) => ({ page: Math.max(1, Number(params.page) || 1), limit: params.limit ?? CONFIG.PAGE_SIZE });

/** 서버가 total_pages를 주지 않아도 화면이 쓸 수 있게 채운다. → { items, total_count, page, page_size, total_pages } */
async function paged(promise, params = {}) {
  const res = await promise;
  const size = res.page_size ?? params.limit ?? CONFIG.PAGE_SIZE;
  return {
    ...res,
    page: res.page ?? page(params).page,
    page_size: size,
    total_pages: res.total_pages ?? Math.max(1, Math.ceil((res.total_count ?? 0) / size)),
  };
}

function emptyPage(params = {}) {
  const normalized = page(params);
  return {
    items: [],
    total_count: 0,
    page: normalized.page,
    page_size: normalized.limit,
    total_pages: 1,
  };
}

async function optionalAnnotationPage(promise, params = {}) {
  try {
    return await paged(promise, params);
  } catch (error) {
    // 메모 API가 아직 배포되지 않은 서버의 FastAPI 기본 404만 빈 목록으로 취급한다.
    // paper_not_found 등 의미가 있는 404는 숨기지 않는다.
    if (error instanceof RouteNotFoundError) {
      return emptyPage(params);
    }
    throw error;
  }
}

function normalizeDevices(response) {
  const raw = Array.isArray(response) ? response : response?.items;
  if (!Array.isArray(raw) || raw.some((device) =>
    typeof device?.device_id !== 'string' || !device.device_id.trim())) {
    throw new ApiError({
      status: 200,
      code: 'invalid_api_response',
      message: '연결 기기 응답을 읽을 수 없어요.',
    });
  }
  return {
    items: raw.map((device) => ({
      ...device,
      name: device.name ?? device.display_name ?? '이름 없는 PC',
      connected_at: device.connected_at ?? device.created_at ?? null,
      last_active_at: device.last_active_at ?? null,
    })),
  };
}

export const api = {
  // ---------- 7. 회원·로그인 ----------
  /** GET /v1/me → { user_id, display_name, email } */
  me: () => send('GET', '/me'),
  /** POST /v1/auth/logout */
  logout: () => send('POST', '/auth/logout'),
  /** GET /v1/auth/login: 인증 화면으로 이동할 주소 */
  loginUrl: (returnTo) => `${CONFIG.API_BASE_URL}/auth/login?provider=google&return_to=${encodeURIComponent(returnTo)}`,
  /** 예시 데이터 전용 로그인 */
  mockLogin: () => send('POST', '/auth/mock-login'),

  /** GET /v1/devices → 배열 또는 { items }; 화면용 필드로 정규화한다. */
  listDevices: async () => normalizeDevices(await send('GET', '/devices')),
  /** DELETE /v1/devices/{기기 ID} */
  disconnectDevice: (deviceId) => send('DELETE', `/devices/${id(deviceId)}`),

  // ---------- 2. 논문 목록 ----------
  /**
   * GET /v1/papers
   * params: q, reading_status(not_started|reading|completed|unknown), sort(recent), collection_id, unassigned, page, limit
   * → { items: PaperListItem[], total_count, page, page_size, total_pages }
   */
  listPapers: (params = {}) => paged(send('GET', '/papers', {
    query: {
      q: params.q?.trim() || undefined,
      reading_status: params.reading_status || undefined,
      sort: params.sort ?? 'recent',
      collection_id: params.collection_id,
      unassigned: params.unassigned ? 'true' : undefined,
      ...page(params),
    },
  }), params),

  // ---------- 3. 논문 상세 ----------
  /** GET /v1/papers/{논문 ID}?learning_record_id=&version_id= → PaperDetail */
  getPaper: (paperId, { learningRecordId, versionId } = {}) => send('GET', `/papers/${id(paperId)}`, {
    query: { learning_record_id: learningRecordId, version_id: versionId },
  }),
  /**
   * GET /v1/papers/{논문 ID}/versions/{원문 버전 ID}/file-access (요청서 9.4)
   * → { status: 'available'|'not_uploaded', url, expires_at, file_name, file_size, content_type }
   * PDF가 없으면 url 없이 status: 'not_uploaded'
   */
  getFileAccess: (paperId, versionId) => send('GET', `/papers/${id(paperId)}/versions/${id(versionId)}/file-access`),

  // ---------- 3·4. 메모·하이라이트 ----------
  /**
   * GET /v1/annotations
   * params: paper_id, version_id, kind(note|highlight), from, to(YYYY-MM-DD, 한국 날짜), q, sort(newest|oldest), page, limit
   * → { items: Annotation[], total_count, page, page_size, total_pages, date_counts: { 'YYYY-MM-DD' | 'unknown': n } }
   */
  listAnnotations: (params = {}) => optionalAnnotationPage(send('GET', '/annotations', {
    query: {
      paper_id: params.paper_id,
      version_id: params.version_id,
      kind: params.kind || undefined,
      from: params.from,
      to: params.to,
      q: params.q?.trim() || undefined,
      sort: params.sort ?? 'newest',
      ...page(params),
    },
  }), params),
  /** GET /v1/annotations/{기록 ID} → Annotation */
  getAnnotation: (annotationId) => send('GET', `/annotations/${id(annotationId)}`),

  // ---------- 5. 컬렉션 목록 ----------
  /** GET /v1/collections → { items: [{ collection_id, name, paper_count, created_at, updated_at }], total_count, page, page_size, total_pages } */
  listCollections: (params = {}) => paged(send('GET', '/collections', { query: page(params) }), params),
  /** POST /v1/collections { name } (중복 요청 키 포함) */
  createCollection: (name, idempotencyKey) => send('POST', '/collections', { body: { name }, idempotencyKey }),
  /** PATCH /v1/collections/{컬렉션 ID} { name } */
  renameCollection: (collectionId, name) => send('PATCH', `/collections/${id(collectionId)}`, { body: { name } }),
  /** DELETE /v1/collections/{컬렉션 ID} */
  deleteCollection: (collectionId) => send('DELETE', `/collections/${id(collectionId)}`),

  // ---------- 6. 컬렉션 상세 ----------
  /** GET /v1/collections/{컬렉션 ID} */
  getCollection: (collectionId) => send('GET', `/collections/${id(collectionId)}`),
  /** GET /v1/collections/{컬렉션 ID}/papers → { items: [{ paper_id, title, file_name, authors, published, added_at }], total_count, page, page_size, total_pages } */
  listCollectionPapers: (collectionId, params = {}) => paged(send('GET', `/collections/${id(collectionId)}/papers`, { query: page(params) }), params),
  /** PUT /v1/collections/{컬렉션 ID}/papers/{논문 ID} → { collection_id, paper_id, paper_count } */
  addPaperToCollection: (collectionId, paperId) => send('PUT', `/collections/${id(collectionId)}/papers/${id(paperId)}`),
  /** DELETE /v1/collections/{컬렉션 ID}/papers/{논문 ID} */
  removePaperFromCollection: (collectionId, paperId) => send('DELETE', `/collections/${id(collectionId)}/papers/${id(paperId)}`),

  // ---------- 10. 학습 이어하기 ----------
  /**
   * POST /v1/resume-requests { paper_id, version_id, learning_record_id, annotation_id? }
   * → { resume_request_id, code, expires_at, prompt_text, status, reason }
   */
  createResumeRequest: (body, idempotencyKey) => send('POST', '/resume-requests', { body, idempotencyKey }),

  // ---------- 개념 지도 (검수 KG) ----------
  /** GET /v1/knowledge-graph/papers → { graph_revision_id, items: [{ paper_key, label, source_url, concept_count }] } */
  listConceptPapers: () => send('GET', '/knowledge-graph/papers'),
  /** GET /v1/knowledge-graph/papers/{논문 키} → { concepts, prerequisites, goals, ... } */
  getConceptGraph: (paperKey) => send('GET', `/knowledge-graph/papers/${id(paperKey)}`),
  /**
   * GET /v1/knowledge-graph/papers/{논문 키}/path?goal=&mode=&known=
   * known: 쉼표로 구분한 아는 개념 → { steps, skipped, unresolved_concepts, goal, mode }
   */
  getConceptPath: (paperKey, { goal, mode, known } = {}) => send('GET', `/knowledge-graph/papers/${id(paperKey)}/path`, {
    query: { goal, mode, known: known?.trim() || undefined },
  }),
};

export { ApiError };
