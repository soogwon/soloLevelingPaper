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

const DEFAULT_MESSAGES = {
  400: '입력값을 확인해 주세요.',
  401: '로그인이 필요해요.',
  403: '접근할 수 없는 기록이에요.',
  404: '기록을 찾을 수 없어요.',
  409: '다른 변경과 충돌했어요. 새로고침 후 다시 시도해 주세요.',
  429: '요청이 많아요. 잠시 후 다시 시도해 주세요.',
};

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
  const data = await response.json().catch(() => null);

  if (!response.ok) {
    const e = data?.error ?? {};
    throw new ApiError({
      status: response.status,
      code: e.code ?? `http_${response.status}`,
      message: e.message ?? DEFAULT_MESSAGES[response.status] ?? '요청을 처리하지 못했어요.',
      field: e.field ?? null,
      requestId: e.request_id ?? response.headers.get('X-Request-Id'),
      details: e.details ?? null,
    });
  }
  return data;
}
