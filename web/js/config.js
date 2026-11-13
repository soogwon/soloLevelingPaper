// 기본은 같은 origin의 실제 /v1 API다. 공개 데모 데이터는 URL에 ?mock=1을 붙였을 때만 사용한다.
const query = new URLSearchParams(window.location.search);
const runtime = globalThis.SOLO_LEVELING_CONFIG ?? {};

export const CONFIG = Object.freeze({
  // 요청서의 /v1 버전 경로. 프론트와 API가 같은 도메인이면 '/v1', 다르면 'https://api.example.com/v1'.
  API_BASE_URL: (runtime.API_BASE_URL ?? '/v1').replace(/\/+$/, ''),
  // 주소에 ?mock=1이 있을 때만 예시 데이터를 사용한다.
  USE_MOCK: query.get('mock') === '1',
  MOCK_LATENCY_MS: 250,
  // 한 페이지에 보여줄 개수. 요청할 때 limit으로 보낸다 (서버 기본값 20, 최대 100: 요청서 1.5).
  PAGE_SIZE: 5,
  // 요청서 1.3: 날짜 표시와 날짜 필터는 한국 시간 기준.
  TIME_ZONE: 'Asia/Seoul',
  // 동일 출처 쿠키 세션 + CSRF 방어(요청서 7.3). 이름은 인증 담당자와 합의해서 바꾼다.
  CSRF_COOKIE: 'csrf_token',
  CSRF_HEADER: 'X-CSRF-Token',
});
