import assert from 'node:assert/strict';
import test from 'node:test';


globalThis.window = { location: { search: '' } };

const { paperRow, paperListHead, sortFromQuery, nextSort } = await import('../js/pages/papers.js');

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
  assert.match(output, /상태 근거: <\/span>원격 학습 기록 · 기준 버전: version-1/);
});

test('읽기 상태 갱신 시각을 한국 시간으로 표시하고 누락을 구분한다', () => {
  const updated = String(paperRow({
    ...paper, reading_status_updated_at: '2026-11-19T16:05:00Z',
  }));
  assert.match(updated, /<span class="row-updated"><span class="cell-label">갱신 시각: <\/span>2026\.11\.20 01:05<\/span>/);
  assert.match(String(paperRow(paper)), /<span class="row-updated"><span class="cell-label">갱신 시각: <\/span>미확인<\/span>/);
});

test('갱신 시각은 상태 근거 칸과 분리된 칸에 표시한다', () => {
  const output = String(paperRow({...paper, reading_status_source: 'remote_file',
    reading_basis_version_id: 'version-1', reading_status_updated_at: '2026-11-19T16:05:00Z'}));
  const basis = output.match(/<span class="row-basis">(.*?)<\/span>\s*<span class="row-updated">/s)?.[1];
  assert.ok(basis);
  assert.doesNotMatch(basis, /갱신 시각|2026\.11\.20/);
});

test('주소의 정렬 값이 올바르지 않으면 최근 학습순 기본 방향으로 돌아간다', () => {
  assert.deepEqual(sortFromQuery({}), { sort: 'recent', order: 'desc' });
  assert.deepEqual(sortFromQuery({ sort: 'title' }), { sort: 'title', order: 'asc' });
  assert.deepEqual(sortFromQuery({ sort: 'updated', order: 'asc' }), { sort: 'updated', order: 'asc' });
  assert.deepEqual(sortFromQuery({ sort: 'author', order: 'up' }), { sort: 'recent', order: 'desc' });
});

test('같은 열을 다시 누르면 방향을 뒤집고 다른 열은 그 열의 기본 방향으로 시작한다', () => {
  assert.deepEqual(nextSort({ sort: 'title', order: 'asc' }, 'title'), { sort: 'title', order: 'desc' });
  assert.deepEqual(nextSort({ sort: 'title', order: 'desc' }, 'updated'), { sort: 'updated', order: 'desc' });
  assert.deepEqual(nextSort({ sort: 'recent', order: 'desc' }, 'status'), { sort: 'status', order: 'asc' });
});

test('열 머리에 정렬 버튼과 현재 정렬 방향을 표시한다', () => {
  const head = String(paperListHead({ sort: 'updated', order: 'desc' }));
  for (const sort of ['title', 'status', 'updated']) assert.match(head, new RegExp(`data-sort="${sort}"`));
  assert.match(head, /class="col-sort is-active" data-sort="updated"/);
  assert.match(head, /갱신 시각 오름차순 정렬 \(현재 내림차순\)/);
  assert.match(head, /상태 근거 · 기준 버전/);
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
