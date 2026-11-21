import assert from 'node:assert/strict';
import test from 'node:test';


globalThis.window = { location: { search: '?mock=1' } };

const { CONFIG } = await import('../js/config.js');
CONFIG.MOCK_LATENCY_MS = 0;
const { ApiError } = await import('../js/api/client.js');
const { api, PAPER_DELETION_NOTICE_VERSION } = await import('../js/api/index.js');
const { submitPaperDeletion, isUncertainDeletionError, deletionAcceptedMessage } = await import('../js/lib/paper-deletion.js');
const { paperRowWithMenu, paperRow } = await import('../js/pages/papers.js');

const KEY = '11111111-2222-4333-8444-555555555555';


test('안내 버전은 서버 deletion_routes.NOTICE_VERSION과 같다', () => {
  assert.equal(PAPER_DELETION_NOTICE_VERSION, 'server-paper-deletion-v1');
});


test('응답을 확인하지 못하면 같은 요청 키로만 다시 보낸다', async () => {
  const keys = [];
  let calls = 0;
  const result = await submitPaperDeletion({
    paperId: 'p1', requestKey: KEY, askRetry: async () => true,
    request: async (paperId, key) => {
      keys.push(key);
      if (++calls === 1) throw new ApiError({ status: 0, code: 'network_error', message: '끊김' });
      return { status: 'blocking' };
    },
  });
  assert.deepEqual(result, { status: 'blocking' });
  assert.deepEqual(keys, [KEY, KEY]);
});


test('사용자가 재전송을 그만두면 null을 돌려준다', async () => {
  const result = await submitPaperDeletion({
    paperId: 'p1', requestKey: KEY, askRetry: async () => false,
    request: async () => { throw new ApiError({ status: 503, code: 'deletion_unavailable', message: 'x' }); },
  });
  assert.equal(result, null);
});


test('확정 오류(409 등)는 재전송하지 않고 그대로 던진다', async () => {
  let asked = false;
  await assert.rejects(submitPaperDeletion({
    paperId: 'p1', requestKey: KEY, askRetry: async () => { asked = true; return true; },
    request: async () => { throw new ApiError({ status: 409, code: 'deletion_request_conflict', message: 'x' }); },
  }), (e) => e.status === 409);
  assert.equal(asked, false);
  assert.equal(isUncertainDeletionError(new ApiError({ status: 404, code: 'x', message: 'x' })), false);
});


test('접수 안내는 완료와 접수를 구분하고 백업 기간을 알린다', () => {
  assert.match(deletionAcceptedMessage({ status: 'blocking' }), /삭제를 요청했어요.*30일/);
  assert.match(deletionAcceptedMessage({ status: 'complete' }), /백업.*30일/);
});


test('목록 행은 링크 밖에 삭제 메뉴를 두고, 검색용 paperRow에는 메뉴가 없다', () => {
  const p = { paper_id: 'paper-1', title: 'Paper <b>', selected_version_id: 'v1', reading_status: 'not_started' };
  const output = String(paperRowWithMenu(p));
  const anchor = output.slice(output.indexOf('<a '), output.indexOf('</a>'));
  assert.doesNotMatch(anchor, /delete-paper/);
  assert.match(output, /data-action="delete-paper" data-paper-id="paper-1"/);
  assert.doesNotMatch(output, /<b>/);
  assert.doesNotMatch(String(paperRow(p)), /delete-paper/);
});


test('예시 서버: 삭제 접수 즉시 목록·상세에서 숨기고 같은 키 재전송은 같은 요청을 돌려준다', async () => {
  const before = await api.listPapers();
  const target = before.items[0].paper_id;

  const first = await api.requestPaperDeletion(target, KEY);
  assert.equal(first.status, 'blocking');
  assert.equal(first.backup_erasure_confirmed, false);
  assert.deepEqual(await api.requestPaperDeletion(target, KEY), first);

  const after = await api.listPapers();
  assert.equal(after.total_count, before.total_count - 1);
  assert.ok(!after.items.some((p) => p.paper_id === target));
  await assert.rejects(api.getPaper(target), (e) => e.status === 404);

  // 이미 삭제를 요청한 논문에 새 키를 붙이면 충돌이다.
  await assert.rejects(api.requestPaperDeletion(target, 'another-request-key-0001'), (e) => e.status === 409);
  // 너무 짧은 키는 서버와 같이 거절한다.
  await assert.rejects(api.requestPaperDeletion(after.items[0].paper_id, 'short'), (e) => e.status === 422);
});
