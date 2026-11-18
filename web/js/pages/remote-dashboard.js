import { request } from '../api/client.js';
import { html, setHTML } from '../lib/html.js';

const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const statuses = { not_started: '시작하지 않음', reading: '읽는 중', completed: '학습 완료' };

export default async function remoteDashboard({ view }) {
  let closed = false, busy = false, generation = 0, items = [], cursor = null;
  const events = ['auth:expired', 'auth:logout', 'hashchange', 'pagehide'];
  function close() {
    closed = true; generation++; items = []; cursor = null; view.textContent = '';
    events.forEach(event => window.removeEventListener(event, close));
  }
  events.forEach(event => window.addEventListener(event, close));
  const current = seq => !closed && view.isConnected && generation === seq;
  function draw(message = '') {
    if (closed || !view.isConnected) return;
    setHTML(view, html`<h1>원격 학습 현황</h1><p role="status">${message}</p>
      <p>열어 본 고정 학습 맥락 목록입니다. 처리 게시 완료와 학습 완료는 별개이며 조회만으로 학습을 시작하지 않습니다.</p>
      <p>맥락 ID 순서로 표시합니다. 학습 변경 시각은 마지막 열람 시각이 아닙니다. 퀴즈·성찰 요약은 아직 지원하지 않습니다.</p>
      ${items.map(item => html`<section class="card panel"><h2>${item.title || '제목 없음'}</h2>
        <p>처리 상태: 게시 완료 · 읽기 상태: ${statuses[item.reading_status]}</p>
        <p>고정 판본: ${item.server_version_id} · 게시: ${item.publication_id}</p>
        <p>최근 PDF 위치: ${item.last_position ? item.last_position.pdf_page + '페이지' : '저장된 위치 없음'}</p>
        <p>학습 변경 시각: ${item.last_learning_updated_at || '없음'}</p>
        <p>메모 ${item.note_count}건 · 하이라이트 ${item.highlight_count}건</p>
        <a href="#/remote-learning/${item.context_id}">이 학습 맥락 열기</a></section>`)}
      ${!busy && items.length === 0 && !message ? html`<p>열어 본 학습 맥락이 없습니다.</p>` : ''}
      <button data-refresh ${busy ? 'disabled' : ''}>새로고침</button>
      ${cursor ? html`<button data-more ${busy ? 'disabled' : ''}>더 보기</button>` : ''}`);
    view.querySelector('[data-refresh]').onclick = () => load(false);
    const more = view.querySelector('[data-more]'); if (more) more.onclick = () => load(true);
  }
  async function load(more) {
    if (closed || busy) return;
    busy = true; const seq = ++generation; draw('조회 중입니다.');
    try {
      const page = await request('GET', '/remote-annotation-products/contexts/dashboard',
        { query: more && cursor ? { after: cursor } : {} });
      if (!current(seq)) return;
      if (!Array.isArray(page.items) || (page.next_cursor !== null && !uuid.test(page.next_cursor))
          || page.items.some(i => !uuid.test(i.context_id) || !Object.hasOwn(statuses, i.reading_status)
            || i.processing_status !== 'published' || !Number.isSafeInteger(i.note_count) || i.note_count < 0
            || !Number.isSafeInteger(i.highlight_count) || i.highlight_count < 0
            || (i.last_position !== null && (!Number.isSafeInteger(i.last_position?.pdf_page) || i.last_position.pdf_page < 1)))) throw Error('shape');
      items = more ? [...new Map([...items, ...page.items].map(i => [i.context_id, i])).values()] : page.items;
      cursor = page.next_cursor; busy = false; draw();
    } catch {
      if (!current(seq)) return;
      busy = false; draw('조회에 실패했습니다. 새로고침으로 다시 확인해 주세요.');
    }
  }
  await load(false);
}
