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

test('동의 관리 항목을 HTML로 그리고 고지 문구만 이스케이프한다', async (t) => {
  const slots = {};
  const view = Object.assign(new EventTarget(), {
    querySelector: selector => (slots[selector] ??= { querySelector: () => null }),
  });
  const item = (purpose, state) => ({ purpose, required: purpose === 'storage', notice: `<b>${purpose}</b>`,
    state, revision: 1, granted_at: null, revoked_at: null });
  t.mock.method(globalThis, 'fetch', async (url) => {
    const path = new URL(url).pathname;
    const body = path.endsWith('/me') ? { display_name: '검증' }
      : path.endsWith('/account/consents') ? { notice_version: 'account-consent-v1', complete: true,
        items: [item('storage', 'current'), item('ai_provider', 'missing')] }
      : [];
    return { ok: true, status: 200, json: async () => body };
  });
  await accountPage({ view });
  const consents = slots['[data-slot="consents"]'].innerHTML;
  assert.equal(consents.match(/<div class="consent-item">/g)?.length, 2);
  assert.doesNotMatch(consents, /&lt;div|<\/div>,/);
  assert.match(consents, /&lt;b&gt;storage&lt;\/b&gt;/);
  assert.match(consents, /data-action="revoke-consent" data-purpose="storage"/);
  assert.match(consents, /data-action="grant-consent" data-purpose="ai_provider"/);
});

test('setHTML은 배열 항목을 쉼표 없이 잇고 문자열은 이스케이프한다', async () => {
  const { html, setHTML } = await import('../js/lib/html.js');
  const el = {};
  setHTML(el, [html`<i>a</i>`, '<b>']);
  assert.equal(el.innerHTML, '<i>a</i>&lt;b&gt;');
});
