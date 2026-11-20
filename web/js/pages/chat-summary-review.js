import { ApiError, request } from '../api/client.js';
import { html, setHTML } from '../lib/html.js';

const fields = [['claim', '연구 질문'], ['method', '핵심 접근'], ['result', '주요 결과']];

export default async function chatSummaryReview({ view, params }) {
  const path = `/remote-summaries/chat-reviews/${encodeURIComponent(params.requestKey)}`;
  let review = null, closed = false, busy = false;
  const events = ['auth:logout', 'auth:expired', 'hashchange', 'pagehide'];
  const close = () => {
    closed = true;
    review = null;
    view.textContent = '';
    events.forEach(event => window.removeEventListener(event, close));
  };
  events.forEach(event => window.addEventListener(event, close));

  function draw(message) {
    if (closed || !view.isConnected) return;
    setHTML(view, html`<h1>Claude 채팅 요약 검토</h1><p role="status">${message}</p>
      ${review ? html`<section class="card panel">
        <p>PDF 판본 ${review.binding.server_version_id}</p>
        <p>요약과 인용을 확인하세요. 저장하면 이 판본의 학습 기록에서 다시 볼 수 있어요.</p>
        ${fields.map(([kind, label]) => {
          const item = review.items.find(entry => entry.kind === kind);
          const pages = [...new Set(item?.evidence_ids.map(id => review.evidence_pages[id])
            .filter(page => Number.isSafeInteger(page) && page > 0))];
          return html`<section><h2>${label}</h2><p>${item?.text ?? '요약에 포함된 근거가 없어요.'}</p>
            ${pages.length ? html`<p>PDF ${pages.join(', ')}쪽</p>` : ''}</section>`;
        })}
        ${review.saved ? html`<a href="#/papers/${encodeURIComponent(review.binding.paper_id)}?version=${encodeURIComponent(review.binding.server_version_id)}">논문 상세 보기</a>`
          : html`<button type="button" data-save ${busy || !review.active ? 'disabled' : ''}>확인하고 요약 저장</button>`}
      </section>` : ''}`);
    const save = view.querySelector('[data-save]');
    if (save) save.onclick = async () => {
      if (busy || closed || !review?.active || review.saved) return;
      busy = true;
      draw('저장 중이에요.');
      try {
        review = await request('POST', `${path}/save`);
        if (closed) return;
        draw('요약을 저장했어요.');
      } catch (error) {
        if (closed) return;
        busy = false;
        if (error instanceof ApiError && error.status === 409) {
          review = { ...review, active: false };
          draw('검토 시간이 지났어요. Claude에서 새 검토를 열어 주세요.');
        } else {
          draw('저장 여부를 확인하지 못했어요. 이 페이지를 새로고침해 확인해 주세요.');
        }
      }
    };
  }

  draw('요약을 불러오는 중이에요.');
  try {
    review = await request('GET', path);
    if (closed) return;
    draw(review.saved ? '이미 저장된 요약이에요.' : review.active ? '' : '검토 시간이 지났어요.');
  } catch {
    draw('이 계정에서 요약 검토를 열 수 없어요.');
  }
}
