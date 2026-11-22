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

for (const [status, badge, position, expected] of [
  ['reading', '읽는 중', { chunk_id: 'chunk-2' }, '청크 chunk-2'],
  ['completed', '읽기 완료', { pdf_page: 3 }, 'PDF 3페이지'],
  ['reading', '읽는 중', { printed_page_label: 'iv' }, '인쇄 페이지 iv'],
  ['completed', '읽기 완료', { section_id: 'sec-2' }, '섹션 sec-2'],
]) {
  test(`${badge} 기록의 ${expected} 위치와 상세 링크를 시각 없이 표시한다`, () => {
    const output = String(paperRow({
      ...paper, reading_status: status, selected_learning_record_id: 'record-1',
      last_position: position, last_studied_at: null,
    }));
    assert.ok(output.includes(expected));
    assert.ok(output.includes(badge));
    assert.match(output, /record=record-1&amp;version=version-1/);
    assert.doesNotMatch(output, /마지막 학습|저장된 학습 위치가 없어요/);
  });
}


test('완료 기준 원문 버전을 선택 버전과 별도로 표시하고 이스케이프한다', () => {
  const output = String(paperRow({...paper, reading_status: 'completed',
    reading_basis_version_id: '<old-version>'}));
  assert.match(output, /&lt;old-version&gt;/);
  assert.doesNotMatch(output, /<old-version>/);
});

test('원격 학습 상태의 근거와 기준 판본을 함께 표시한다', () => {
  const output = String(paperRow({...paper, reading_status: 'reading',
    reading_status_source: 'remote_learning', reading_basis_version_id: 'version-1'}));
  assert.match(output, /상태 근거: 원격 학습 기록 · 기준 버전: version-1/);
});

test('읽기 상태 갱신 시각을 한국 시간으로 표시하고 누락을 구분한다', () => {
  const updated = String(paperRow({
    ...paper, reading_status_updated_at: '2026-11-19T16:05:00Z',
  }));
  assert.match(updated, /갱신 시각: 2026\.11\.20 01:05/);
  assert.match(String(paperRow(paper)), /갱신 시각 미확인/);
});


test('PDF에서 찾은 논문 제목을 표시하고 파일 이름은 보조 정보로 남긴다', () => {
  const output = String(paperRow({
    ...paper, title: 'Attention Is All You Need', file_name: '1706.03762v7.pdf',
  }));
  assert.match(output, /<span class="row-title">Attention Is All You Need<\/span>/);
  assert.match(output, /저자·출판 연도 정보 없음 · 1706\.03762v7\.pdf/);
});


test('제목을 찾지 못하면 파일 이름을 제목으로 쓰고 보조 정보에 반복하지 않는다', () => {
  const output = String(paperRow({ ...paper, title: null, file_name: '1706.03762v7.pdf' }));
  assert.match(output, /<span class="row-title">1706\.03762v7\.pdf<\/span>/);
  assert.equal(output.split('1706.03762v7.pdf').length - 1, 1);
});
