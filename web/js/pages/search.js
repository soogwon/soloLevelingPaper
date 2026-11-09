// 11. 상단 검색: 논문 목록 API(제목) + 기록 조회 API(본문·인용·제목)를 조합한다.
import { api } from '../api/index.js';
import { html, setHTML } from '../lib/html.js';
import { buildHash } from '../router.js';
import { loadingBlock, emptyBlock, errorBlock } from '../lib/ui.js';
import { fullAnnotationCard } from '../lib/annotation.js';
import { paperRow } from './papers.js';

const PREVIEW = 5;

export default async function searchPage({ view, query }) {
  const q = (query.q ?? '').trim();
  document.title = `‘${q}’ 검색 · Solo Leveling`;
  if (!q) {
    setHTML(view, emptyBlock('검색어를 입력해 주세요', '논문 제목이나 메모 내용으로 찾을 수 있어요.'));
    return;
  }

  setHTML(view, html`
    <div class="page-head"><div><h1 class="page-title">‘${q}’ 검색 결과</h1><p class="page-desc">논문과 메모·하이라이트 결과를 따로 보여줘요.</p></div></div>
    <section class="result-section" aria-labelledby="r-papers">
      <div class="result-head"><h2 id="r-papers">논문 <span class="meta" data-slot="paper-count"></span></h2><span data-slot="paper-all"></span></div>
      <div class="card list" data-slot="papers">${loadingBlock()}</div>
    </section>
    <section class="result-section" aria-labelledby="r-notes">
      <div class="result-head"><h2 id="r-notes">메모·하이라이트 <span class="meta" data-slot="note-count"></span></h2><span data-slot="note-all"></span></div>
      <div class="stack" data-slot="notes">${loadingBlock()}</div>
    </section>`);

  const slot = (name) => view.querySelector(`[data-slot="${name}"]`);

  // 두 결과의 개수와 다음 페이지는 각각 관리한다 (요청서 11).
  const loadPapers = api.listPapers({ q, limit: PREVIEW }).then((res) => {
    slot('paper-count').textContent = `${res.total_count}건`;
    setHTML(slot('papers'), res.items.length ? html`${res.items.map(paperRow)}` : emptyBlock('제목이 일치하는 논문이 없어요'));
    if (res.total_pages > 1) setHTML(slot('paper-all'), html`<a href="${buildHash('/papers', { q })}">논문 결과 모두 보기</a>`);
  }).catch((error) => setHTML(slot('papers'), errorBlock(error)));

  const loadNotes = api.listAnnotations({ q, limit: PREVIEW }).then((res) => {
    slot('note-count').textContent = `${res.total_count}건`;
    setHTML(slot('notes'), res.items.length
      ? html`${res.items.map((a) => fullAnnotationCard(a, { showDate: true }))}`
      : html`<div class="card">${emptyBlock('일치하는 메모·하이라이트가 없어요')}</div>`);
    if (res.total_pages > 1) setHTML(slot('note-all'), html`<a href="${buildHash('/notes', { q })}">메모 결과 모두 보기</a>`);
  }).catch((error) => setHTML(slot('notes'), errorBlock(error)));

  await Promise.all([loadPapers, loadNotes]);
}
