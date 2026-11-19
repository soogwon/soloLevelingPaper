import { api } from '../api/index.js';
import { CONFIG } from '../config.js';
import { paperTitle } from '../lib/format.js';
import { html, setHTML } from '../lib/html.js';
import { hasFirstLearningSource, remoteFirstLearning } from '../lib/remote-first-learning.js';
import { emptyBlock } from '../lib/ui.js';
import { buildHash } from '../router.js';

export default async function paperLearningStartPage({ view, params, query }) {
  const back = buildHash(`/papers/${encodeURIComponent(params.paperId)}`, {version:query.version});
  let closed = false;
  const events = ['auth:logout', 'auth:expired', 'hashchange', 'pagehide'];
  const close = () => {
    closed = true;
    view.textContent = '';
    events.forEach(event => window.removeEventListener(event, close));
  };
  events.forEach(event => window.addEventListener(event, close));
  const paper = await api.getPaper(params.paperId, {versionId:query.version});
  if (closed || !view.isConnected) return;
  const title = paperTitle(paper);
  const versionId = paper.selected_version_id;
  document.title = `Claude에서 학습 시작 · ${title} · Solo Leveling`;
  setHTML(view, html`
    <nav class="breadcrumb" aria-label="현재 위치">
      <a href="#/papers">논문 아카이브</a><span aria-hidden="true">/</span>
      <a href="${back}">논문 상세</a><span aria-hidden="true">/</span><span>학습 시작</span>
    </nav>
    <div class="detail-head">
      <div class="detail-head-main">
        <h1 class="detail-title">Claude에서 학습 시작</h1>
        <p class="meta">${title}</p>
      </div>
    </div>
    <section class="card panel learning-start-panel" data-slot="first-learning"></section>
  `);
  const panel = view.querySelector('[data-slot="first-learning"]');
  if (CONFIG.USE_MOCK || !hasFirstLearningSource(paper)) {
    setHTML(panel, emptyBlock('학습을 시작할 수 없어요', '서버에서 검증을 마친 PDF가 필요해요.'));
    return;
  }
  await remoteFirstLearning(panel, paper.paper_id, versionId);
}
