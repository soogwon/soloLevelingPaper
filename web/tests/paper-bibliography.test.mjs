import assert from 'node:assert/strict';
import test from 'node:test';

globalThis.window = { location: { search: '' } };

const { formatAllAuthorsYear, formatAuthorsYear, paperFileNameHint } = await import('../js/lib/format.js');
const { paperRow } = await import('../js/pages/papers.js');

const paper = {
  paper_id: 'paper-1', title: 'Attention Is All You Need', file_name: '1706.03762v7.pdf',
  authors: ['Ashish Vaswani', 'Noam Shazeer'], published: { year: 2017, month: null, day: null, precision: 'year' },
  selected_version_id: 'v1', reading_status: 'not_started',
};

test('목록은 첫 저자 요약과 연도, 상세는 저자 전체 이름과 연도를 보여 준다', () => {
  assert.equal(formatAuthorsYear(paper), 'Vaswani 외 · 2017');
  assert.equal(formatAllAuthorsYear(paper), 'Ashish Vaswani, Noam Shazeer · 2017');
  assert.match(String(paperRow(paper)), /Vaswani 외 · 2017 · 1706\.03762v7\.pdf/);
});

test('저자·연도가 없으면 상세 보조 줄을 비우고 파일 이름 힌트는 제목과 다를 때만 준다', () => {
  assert.equal(formatAllAuthorsYear({ authors: null, published: null }), null);
  assert.equal(paperFileNameHint(paper), '1706.03762v7.pdf');
  assert.equal(paperFileNameHint({ ...paper, title: null }), null);
  assert.equal(paperFileNameHint({ ...paper, file_name: null }), null);
});
