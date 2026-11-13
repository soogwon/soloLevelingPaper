import assert from 'node:assert/strict';
import test from 'node:test';


globalThis.window = { location: { search: '' } };

const { paperRow } = await import('../js/pages/papers.js');

const paper = {
  paper_id: 'paper-1', title: 'Paper', authors: null, published: null,
  selected_version_id: 'version-1', reading_status: 'not_started',
};


test('학습 기록이 있지만 위치와 학습 시각이 없으면 위치 미저장으로 표시한다', () => {
  const output = String(paperRow({
    ...paper, selected_learning_record_id: 'record-1', last_position: null,
    last_studied_at: null,
  }));

  assert.match(output, /아직 저장된 학습 위치가 없어요/);
  assert.doesNotMatch(output, /아직 저장된 학습 기록이 없어요/);
});


test('학습 기록 자체가 없을 때만 기록 없음으로 표시한다', () => {
  const output = String(paperRow({
    ...paper, selected_learning_record_id: null, last_position: null,
    last_studied_at: null,
  }));

  assert.match(output, /아직 저장된 학습 기록이 없어요/);
});


test('위치 없이 마지막 학습 시각만 있으면 위치 미저장과 날짜를 표시한다', () => {
  const output = String(paperRow({
    ...paper, selected_learning_record_id: 'record-1', last_position: null,
    last_studied_at: '2026-11-13T12:00:00Z',
  }));

  assert.match(output, /학습 위치가 저장되지 않았어요/);
  assert.match(output, /마지막 학습 2026\.11\.13/);
});
