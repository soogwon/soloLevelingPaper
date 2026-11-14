import { CONFIG } from '../config.js';

/** API 오류. 요청서 1.6: 오류 코드, 사용자 메시지, 문제 입력 항목, 요청 ID */
export class ApiError extends Error {
  constructor({ status, code, message, field = null, requestId = null, details = null }) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.field = field;
    this.requestId = requestId;
    this.details = details;
  }
}

export class RouteNotFoundError extends ApiError {}

const DEFAULT_MESSAGES = {
  400: '입력값을 확인해 주세요.',
  401: '로그인이 필요해요.',
  403: '접근할 수 없는 기록이에요.',
  404: '기록을 찾을 수 없어요.',
  409: '다른 변경과 충돌했어요. 새로고침 후 다시 시도해 주세요.',
  410: '유효 기간이 지났어요. 다시 시도해 주세요.',
  413: '파일이 너무 커요.',
  422: '입력값을 확인해 주세요.',
  429: '요청이 많아요. 잠시 후 다시 시도해 주세요.',
  500: '서버에서 문제가 생겼어요. 잠시 후 다시 시도해 주세요.',
  503: '서버를 잠시 사용할 수 없어요. 잠시 후 다시 시도해 주세요.',
};

/**
 * 서버는 오류를 { detail: { code } }처럼 코드만 보내고 메시지는 보내지 않는다.
 * 코드별로 화면에 보여줄 문구를 여기서 정한다. 없는 코드는 상태별 기본 문구(DEFAULT_MESSAGES)를 쓴다.
 * 코드 목록: src/solo_leveling/interfaces/http/*_routes.py
 */
const CODE_MESSAGES = {
  // 로그인·세션
  authentication_required: '로그인이 필요해요.',
  authentication_failed: '로그인하지 못했어요. 다시 시도해 주세요.',
  authentication_unavailable: '로그인 서버에 연결할 수 없어요. 잠시 후 다시 시도해 주세요.',
  web_session_required: '웹에서 다시 로그인해 주세요.',
  csrf_validation_failed: '보안 확인에 실패했어요. 새로고침 후 다시 시도해 주세요.',
  // 컬렉션
  collection_name_conflict: '같은 이름의 컬렉션이 이미 있어요.',
  invalid_collection_name: '컬렉션 이름은 1~100자로 입력해 주세요.',
  collection_membership_conflict: '이미 다른 컬렉션에 들어 있는 논문이에요.',
  collection_not_found: '컬렉션을 찾을 수 없어요. 이미 삭제됐을 수 있어요.',
  collection_paper_not_found: '컬렉션에서 논문을 찾을 수 없어요. 이미 빠졌을 수 있어요.',
  collection_filter_conflict: '컬렉션 조건이 서로 맞지 않아요.',
  collection_store_unavailable: '컬렉션을 불러올 수 없어요. 잠시 후 다시 시도해 주세요.',
  // 논문·학습 기록
  paper_not_found: '논문을 찾을 수 없어요.',
  paper_version_not_found: '이 버전의 논문 원문을 찾을 수 없어요.',
  learning_record_not_found: '학습 기록을 찾을 수 없어요.',
  invalid_paper_query: '검색 조건을 확인해 주세요.',
  paper_store_unavailable: '논문 기록을 불러올 수 없어요. 잠시 후 다시 시도해 주세요.',
  // 메모·하이라이트
  annotation_not_found: '메모·하이라이트를 찾을 수 없어요.',
  invalid_annotation_date_range: '시작 날짜가 끝 날짜보다 늦어요. 기간을 다시 선택해 주세요.',
  invalid_annotation_query: '검색 조건을 확인해 주세요.',
  annotation_store_unavailable: '메모·하이라이트를 불러올 수 없어요. 잠시 후 다시 시도해 주세요.',
  // PDF
  invalid_version_id: '논문 원문 정보가 올바르지 않아요.',
  pdf_access_unavailable: 'PDF를 열 수 없어요. 잠시 후 다시 시도해 주세요.',
  pdf_storage_quota_exceeded: 'PDF 저장 공간이 부족해요.',
  pdf_upload_unavailable: 'PDF를 올릴 수 없어요. 잠시 후 다시 시도해 주세요.',
  // 연결 기기
  device_not_found: '연결된 PC를 찾을 수 없어요. 이미 해제됐을 수 있어요.',
  device_store_unavailable: '연결된 PC 목록을 불러올 수 없어요. 잠시 후 다시 시도해 주세요.',
  // 학습 이어하기
  resume_target_not_found: '이어서 학습할 기록을 찾을 수 없어요.',
  resume_request_expired: '이어하기 코드가 만료됐어요. 새로 만들어 주세요.',
  resume_store_unavailable: '이어하기 코드를 만들 수 없어요. 잠시 후 다시 시도해 주세요.',
  // 중복 요청 방지
  idempotency_conflict: '같은 요청이 이미 처리 중이에요. 새로고침 후 확인해 주세요.',
  invalid_idempotency_key: '요청을 처리하지 못했어요. 새로고침 후 다시 시도해 주세요.',
};

function isPathValidationError(data) {
  return Array.isArray(data?.detail) && data.detail.some((d) => d?.loc?.[0] === 'path');
}

function readCookie(name) {
  const match = document.cookie.split('; ').find((c) => c.startsWith(name + '='));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

/**
 * 실제 서버 요청.
 * 오류 응답 본문은 { "error": { "code", "message", "field", "request_id" } } 형태로 가정한다.
 * 백엔드가 다른 형태를 쓰면 이 함수의 오류 변환 부분만 고치면 된다.
 */
export async function request(method, path, { query, body, idempotencyKey } = {}) {
  const url = new URL(CONFIG.API_BASE_URL.replace(/\/$/, '') + path, window.location.origin);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value);
    }
  }

  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') {
    const token = readCookie(CONFIG.CSRF_COOKIE);
    if (token) headers[CONFIG.CSRF_HEADER] = token;
  }
  if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;

  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      credentials: 'include',
    });
  } catch {
    throw new ApiError({ status: 0, code: 'network_error', message: '서버에 연결할 수 없어요.' });
  }

  if (response.status === 204) return null;
  let data;
  try {
    data = await response.json();
  } catch {
    if (response.ok) {
      throw new ApiError({ status: response.status, code: 'invalid_api_response',
        message: '서버 응답을 읽을 수 없어요. API 연결 설정을 확인해 주세요.' });
    }
  }

  if (!response.ok) {
    // 현재 FastAPI는 { detail: { code } }, 향후 공통 오류 envelope는 { error: {...} }를 쓴다.
    const e = data?.error ?? data?.detail ?? {};
    const ErrorType = response.status === 404 && data?.detail === 'Not Found'
      && Object.keys(data).length === 1 ? RouteNotFoundError : ApiError;
    throw new ErrorType({
      status: response.status,
      code: e.code ?? `http_${response.status}`,
      message: e.message ?? CODE_MESSAGES[e.code]
        // 주소의 ID 형식이 틀리면 FastAPI가 422로 거절한다 (loc: ['path', ...]). 사용자에게는 '없는 기록'과 같다.
        ?? (isPathValidationError(data) ? DEFAULT_MESSAGES[404] : null)
        ?? DEFAULT_MESSAGES[response.status] ?? '요청을 처리하지 못했어요.',
      field: e.field ?? null,
      requestId: e.request_id ?? response.headers.get('X-Request-Id'),
      details: e.details ?? null,
    });
  }
  return data;
}
