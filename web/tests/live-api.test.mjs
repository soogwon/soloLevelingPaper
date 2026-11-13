import assert from 'node:assert/strict';
import test from 'node:test';


globalThis.window = {
  location: { search: '', origin: 'https://app.example.com' },
  dispatchEvent() {},
};
globalThis.document = { cookie: '' };
globalThis.CustomEvent = class CustomEvent {};

const calls = [];
globalThis.fetch = async (url, options) => {
  calls.push({ url: String(url), options });
  const path = new URL(url).pathname;
  const body = path.endsWith('/papers')
    ? { items: [], page: 1, page_size: 20, total_count: 0, total_pages: 0 }
    : {
        paper_id: '00000000-0000-0000-0000-000000000001',
        selected_learning_record_id: null,
        selected_version_id: null,
        summary: null,
      };
  return { ok: true, status: 200, json: async () => body };
};

const { CONFIG } = await import('../js/config.js');
const { api } = await import('../js/api/index.js');


test('production default uses the live same-origin API', async () => {
  assert.equal(CONFIG.USE_MOCK, false);
  assert.equal(CONFIG.API_BASE_URL, '/v1');

  await api.listPapers({ page: 1, limit: 20, reading_status: 'reading' });
  await api.getPaper('00000000-0000-0000-0000-000000000001');

  assert.equal(calls.length, 2);
  const listUrl = new URL(calls[0].url);
  assert.equal(listUrl.pathname, '/v1/papers');
  assert.equal(listUrl.searchParams.get('reading_status'), 'reading');
  assert.equal(listUrl.searchParams.get('page'), '1');
  assert.equal(listUrl.searchParams.get('limit'), '20');
  assert.equal(new URL(calls[1].url).pathname,
    '/v1/papers/00000000-0000-0000-0000-000000000001');
  assert.ok(calls.every(({ options }) => options.credentials === 'include'));
});


test('live client preserves the current server detail error code', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: false,
    status: 404,
    headers: { get: () => null },
    json: async () => ({ detail: { code: 'paper_not_found' } }),
  });
  try {
    await assert.rejects(
      api.getPaper('00000000-0000-0000-0000-000000000099'),
      (error) => error.status === 404 && error.code === 'paper_not_found',
    );
  } finally {
    globalThis.fetch = previous;
  }
});

test('현재 서버의 배열형 기기 응답을 계정 화면 계약으로 정규화한다', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => ({
    ok: true,
    status: 200,
    json: async () => [{
      device_id: 'device-1',
      display_name: 'My PC',
      created_at: '2026-11-13T00:00:00Z',
    }],
  }));

  assert.deepEqual(await api.listDevices(), {
    items: [{
      device_id: 'device-1',
      display_name: 'My PC',
      created_at: '2026-11-13T00:00:00Z',
      name: 'My PC',
      connected_at: '2026-11-13T00:00:00Z',
      last_active_at: null,
    }],
  });
});

test('기기 응답 형태가 잘못되면 빈 목록으로 숨기지 않는다', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => ({
    ok: true,
    status: 200,
    json: async () => ({}),
  }));

  await assert.rejects(api.listDevices(), { code: 'invalid_api_response' });
});

test('미배포 annotations 경로의 기본 404는 빈 목록으로 표시한다', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => ({
    ok: false,
    status: 404,
    headers: { get: () => null },
    json: async () => ({ detail: 'Not Found' }),
  }));

  assert.deepEqual(await api.listAnnotations({ page: 1, limit: 3 }), {
    items: [], total_count: 0, page: 1, page_size: 3, total_pages: 1,
  });
});

test('논문 없음처럼 의미가 있는 annotations 404는 그대로 전달한다', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => ({
    ok: false,
    status: 404,
    headers: { get: () => null },
    json: async () => ({ detail: { code: 'paper_not_found' } }),
  }));

  await assert.rejects(api.listAnnotations({ paper_id: 'missing' }), {
    status: 404, code: 'paper_not_found',
  });
});

test('HTML을 반환하는 정적 서버를 정상 API 응답으로 처리하지 않는다', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => new Response('<html></html>'));
  await assert.rejects(api.me(), { code: 'invalid_api_response' });
});

test('API 주소 끝에 슬래시가 있어도 로그인과 조회에 같은 기본 주소를 쓴다', async () => {
  globalThis.SOLO_LEVELING_CONFIG = { API_BASE_URL: 'https://api.example.com/v1/', USE_MOCK: true };
  try {
    const { CONFIG: runtime } = await import('../js/config.js?runtime-test');
    assert.equal(runtime.API_BASE_URL, 'https://api.example.com/v1');
    assert.equal(runtime.USE_MOCK, false);
  } finally {
    delete globalThis.SOLO_LEVELING_CONFIG;
  }
});
