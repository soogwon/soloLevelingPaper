import { request } from '../api/client.js';
import { html, setHTML } from './html.js';

const labels = { queued: '대기', processing: '처리 중', completed: '처리 완료', failed: '처리 실패', cancelled: '취소됨' };
const uuid = v => typeof v === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(v);

// 처리 상태는 학습 완료·최근 학습 위치와 별도로 표시한다.
export async function remotePaperJobs(view, paper, version) {
  if (!view || !uuid(paper)) return;
  if (typeof version !== 'string' || !version.trim() || version.length > 128) {
    setHTML(view, html`<h2>처리 작업</h2><p>원문 판본을 선택하면 처리 작업을 조회할 수 있습니다.</p>`); return;
  }
  let closed = false, busy = false, items = [], next = null, seq = 0;
  const events = ['auth:logout', 'auth:expired', 'hashchange', 'pagehide'];
  const close = () => { closed = true; seq++; items = []; view.textContent = ''; events.forEach(e => window.removeEventListener(e, close)); };
  events.forEach(e => window.addEventListener(e, close));
  const current = n => !closed && view.isConnected && n === seq;
  function draw(message = '') {
    if (closed || !view.isConnected) return;
    setHTML(view, html`<h2>처리 작업</h2><p>선택한 원문 판본: ${version}</p>
      <p>처리 완료는 학습 완료가 아닙니다. 취소해도 기존 게시 결과와 학습 기록은 유지되며, 이미 발생한 외부 호출·비용은 되돌리지 않습니다.</p>
      <p role="status">${message}</p>
      ${items.map(j => html`<section><h3>${labels[j.status]}</h3><p>작업 ${j.job_id} · revision ${j.revision}</p>
        <p>${j.file_name || j.title || '제목 없음'}</p>${j.error_code ? html`<p>오류 코드: ${j.error_code}</p>` : ''}</section>`)}
      ${next ? html`<button data-jobs-more ${busy ? 'disabled' : ''}>처리 작업 더 보기</button>` : ''}
      <button data-jobs-refresh ${busy ? 'disabled' : ''}>처리 상태 다시 조회</button>`);
    const more = view.querySelector('[data-jobs-more]'); if (more) more.onclick = () => load(true);
    view.querySelector('[data-jobs-refresh]').onclick = () => load(false);
  }
  async function load(append) {
    if (busy || closed) return;
    busy = true; const n = ++seq; draw('조회 중입니다.');
    try {
      const page = await request('GET', `/papers/${paper}/processing-jobs`, { query: { version_id: version, after: append ? next : undefined } });
      if (!current(n)) return;
      if (page.status !== 'ok' || !Array.isArray(page.jobs) || page.jobs.some(j => !uuid(j.job_id)
          || j.paper_id !== paper || j.server_version_id !== version || !Object.hasOwn(labels, j.status)
          || !Number.isSafeInteger(j.revision) || j.revision < 1) || (page.next_after != null && !uuid(page.next_after))) throw Error('scope');
      items = append ? [...new Map([...items, ...page.jobs].map(j => [j.job_id, j])).values()] : page.jobs;
      next = page.next_after; busy = false; draw(items.length ? '' : '이 판본의 처리 작업이 없습니다.');
    } catch {
      if (!current(n)) return;
      items = []; next = null; busy = false;
      draw('처리 상태를 확인하지 못했습니다. 로그인과 기능 활성화 상태를 확인한 뒤 다시 조회하세요. 새 작업이나 취소 요청은 만들지 않습니다.');
    }
  }
  await load(false);
}
