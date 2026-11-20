import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = Object.assign(new EventTarget(), { location: { origin: 'https://example.test', search: '' } });
globalThis.document = { cookie: '' };
const { default: accountPage } = await import('../js/pages/account.js');

for (const localCount of [0, 1]) {
  test(`원격 연결을 기본 표시하고 로컬 기기 ${localCount}대의 토글을 처리한다`, async (t) => {
    const remote = {}, local = { querySelector: () => null };
    const view = Object.assign(new EventTarget(), {
      querySelector: selector => selector.includes('remote-connections') ? remote : local,
    });
    t.mock.method(globalThis, 'fetch', async (url) => {
      const path = new URL(url).pathname;
      const body = path.endsWith('/me') ? { display_name: '검증' }
        : path.endsWith('/remote-mcp') ? [{ linked_at: '2026-11-20T01:00:00Z' }]
        : Array.from({ length: localCount }, () => ({
          device_id: 'fixture', display_name: '테스트 PC', created_at: '2026-11-20T01:00:00Z',
        }));
      return { ok: true, status: 200, json: async () => body };
    });
    await accountPage({ view });
    assert.match(view.innerHTML, /원격 MCP 연결[\s\S]*data-slot="devices"/);
    assert.match(remote.innerHTML, /연결됨/);
    if (localCount === 0) {
      assert.equal(local.innerHTML, '');
    } else {
      assert.match(local.innerHTML, /<details class="card panel processing-jobs"\s*>/);
      assert.match(local.innerHTML, /로컬 기기 \(1\)/);
      assert.match(local.innerHTML, /테스트 PC/);
    }
  });
}
