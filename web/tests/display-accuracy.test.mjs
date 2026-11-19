import assert from 'node:assert/strict';
import test from 'node:test';

globalThis.window = {location:{search:''}};
globalThis.document = {cookie:''};

const {normalizeDevices} = await import('../js/api/index.js');
const {summarySection} = await import('../js/pages/paper-detail.js');

test('기기 API가 최근 활동 필드를 제공하지 않은 경우를 활동 기록 없음과 구분한다', () => {
  const missing = normalizeDevices([{device_id:'d1',display_name:'PC',created_at:'2026-11-01'}]).items[0];
  assert.equal(missing.last_active_at, null);
  assert.equal(missing.last_active_available, false);

  const empty = normalizeDevices([{device_id:'d2',display_name:'PC',created_at:'2026-11-01',last_active_at:null}]).items[0];
  assert.equal(empty.last_active_at, null);
  assert.equal(empty.last_active_available, true);
});

test('요약에 근거 ID만 있으면 실제 인용을 불러온 것처럼 표시하지 않는다', () => {
  const output = String(summarySection({items:[{
    kind:'claim',text:'주장',evidence_ids:['e1','e2'],
  }]}));
  assert.match(output, /근거 ID 2개 저장됨/);
  assert.match(output, /상세 인용은 아직 불러오지 않음/);
  assert.doesNotMatch(output, /근거 2개 보기/);
});
