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
