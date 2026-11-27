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


test('위치 없이 마지막 학습 시각만 있으면 위치 미저장과 학습 시각 열을 표시한다', () => {
  const output = String(paperRow({
    ...paper, selected_learning_record_id: 'record-1', last_position: null,
    last_studied_at: '2026-11-13T12:00:00Z',
  }));

  assert.match(output, /학습 위치가 저장되지 않았어요/);
  assert.match(output, /<span class="cell-label">학습 시각: <\/span>2026\.11\.13 21:00<\/span>/);
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


test('목록에는 상태 근거와 기준 버전 열을 두지 않는다', () => {
  const output = String(paperRow({...paper, reading_status: 'reading',
    reading_status_source: 'remote_learning', reading_basis_version_id: 'version-1'}));
  assert.doesNotMatch(output, /상태 근거|기준 버전|row-basis/);
  assert.doesNotMatch(String(paperListHead({ sort: 'recent', order: 'desc' })), /상태 근거|기준 버전/);
});

test('읽기 상태 갱신 시각은 이름 아래 줄에 한국 시간으로 표시한다', () => {
  const updated = String(paperRow({
    ...paper, reading_status_updated_at: '2026-11-19T16:05:00Z',
  }));
  assert.match(updated, /<span class="row-line">[^<]* · <span class="nowrap">갱신 시각 2026\.11\.20 01:05<\/span><\/span>/);
  assert.doesNotMatch(String(paperRow(paper)), /갱신 시각/);
  assert.match(String(paperRow(paper)), /<span class="cell-label">학습 시각: <\/span>기록 없음<\/span>/);
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
  const head = String(paperListHead({ sort: 'recent', order: 'desc' }));
  for (const sort of ['title', 'status', 'recent']) assert.match(head, new RegExp(`data-sort="${sort}"`));
  assert.match(head, /class="col-sort is-active" data-sort="recent"/);
  assert.match(head, /학습 시각 오름차순 정렬 \(현재 내림차순\)/);
  assert.match(head, /처리 상태/);
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

test('로컬 기록 없이 원격 학습만 했으면 원격 학습 기록과 마지막 학습 날짜를 표시한다', () => {
  const output = String(paperRow({...paper, selected_learning_record_id: null, last_position: null,
    last_studied_at: null, reading_status: 'reading', reading_status_source: 'remote_learning',
    reading_status_updated_at: '2026-11-21T05:39:00Z'}));
  assert.match(output, /Claude 원격 학습 기록이 있어요 · <span class="nowrap">갱신 시각 2026\.11\.21 14:39/);
  assert.doesNotMatch(output, /아직 저장된 학습 기록이 없어요/);
});

test('원격 원문만 등록된 논문은 학습 기록 없음으로 표시한다', () => {
  const output = String(paperRow({...paper, selected_learning_record_id: null, last_position: null,
    last_studied_at: null, reading_status_source: 'remote_file',
    reading_status_updated_at: '2026-11-21T05:39:00Z'}));
  assert.match(output, /아직 저장된 학습 기록이 없어요/);
  assert.match(output, /기록 없음/);
});

test('원격 학습 시각을 학습 시각 열에 표시한다', () => {
  const output = String(paperRow({...paper, last_studied_at: null, reading_status_source: 'remote_learning',
    last_learning_at: '2026-11-21T05:41:00Z', reading_status_updated_at: '2026-11-21T05:39:00Z'}));
  assert.match(output, /<span class="cell-label">학습 시각: <\/span>2026\.11\.21 14:41<\/span>/);
});

for (const [status, label] of [['none', '처리 작업 없음'], ['queued', '처리 대기'], ['running', '처리 중'],
  ['completed', '처리 완료'], ['failed', '처리 실패'], ['cancelled', '처리 취소'], [undefined, '처리 작업 없음']]) {
  test(`처리 상태 ${status}를 ${label}로 표시한다`, () => {
    const output = String(paperRow({...paper, processing_status: status}));
    assert.match(output, new RegExp(`<span class="row-processing">.*${label}</span></span>`, 's'));
  });
}
