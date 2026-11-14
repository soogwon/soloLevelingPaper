// 개념 지도 — GET /v1/knowledge-graph/... (팀이 검수한 개념·선수 관계)
// 목록(#/concepts)과 논문별 지도(#/concepts/{논문 키}?goal=&mode=&known=)를 한 화면 모듈로 그린다.
// 개인 known 값은 서버에 저장하지 않고 이번 조회에만 쓴다. 건너뛴 개념은 이해했다는 뜻이 아니다.
import { api } from '../api/index.js';
import { html, esc, setHTML } from '../lib/html.js';
import { replaceQuery } from '../router.js';
import { loadingBlock, emptyBlock, errorBlock, copyText, toast } from '../lib/ui.js';

const GOALS = [['understand', '이해'], ['implement', '구현'], ['skim', '훑어보기']];
const MODES = [['compact', '필수만'], ['expanded', '선택 포함']];
const ROLE_LABEL = { target: '목표', required: '필수 선수', optional: '선택' };
const SKIP_LABEL = { known: '안다고 표시', only_needed_by_known: '아는 개념에만 필요' };

const NODE_W = 156;
const NODE_H = 46;
const COL_GAP = 64;
const ROW_GAP = 14;
const PAD = 16;

export default async function conceptsPage({ view, params, query }) {
  if (!params.paperKey) return listPage(view);
  return graphPage(view, params.paperKey, query);
}

async function listPage(view) {
  document.title = '개념 지도 · Solo Leveling';
  const data = await api.listConceptPapers();
  setHTML(view, html`
    <div class="page-head">
      <div>
        <h1 class="page-title">개념 지도</h1>
        <p class="page-desc">팀이 검수한 개념과 선수 관계로, 논문을 읽기 전에 무엇을 어떤 순서로 알면 좋은지 보여줘요.</p>
      </div>
    </div>
    ${data.items.length ? html`<ul class="kg-paper-list">${data.items.map((p) => html`
      <li><a class="card kg-paper-card" href="#/concepts/${encodeURIComponent(p.paper_key)}">
        <span class="kg-paper-title">${p.label}</span>
        <span class="hint">개념 ${p.concept_count}개</span>
      </a></li>`)}</ul>`
      : emptyBlock('아직 개념 지도가 있는 논문이 없어요.')}
  `);
}

function readState(query) {
  return {
    goal: GOALS.some(([v]) => v === query.goal) ? query.goal : 'understand',
    mode: MODES.some(([v]) => v === query.mode) ? query.mode : 'compact',
    known: (query.known ?? '').slice(0, 1000),
  };
}

function sourceLink(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

// 경로에 나온 개념(단계와 건너뜀)만 남긴 그래프. 비어 있으면 전체를 그린다.
function focusGraph(graph, path) {
  if (!path) return graph;
  const keep = new Set([...path.steps, ...path.skipped].map((s) => s.concept_id));
  if (!keep.size) return graph;
  return {
    ...graph,
    concepts: graph.concepts.filter((c) => keep.has(c.concept_id)),
    prerequisites: graph.prerequisites.filter((e) => keep.has(e.concept_id) && keep.has(e.prerequisite_id)),
  };
}

async function graphPage(view, paperKey, query) {
  const state = readState(query);
  const graph = await api.getConceptGraph(paperKey);
  const source = sourceLink(graph.source_url);
  document.title = `${graph.label} 개념 지도 · Solo Leveling`;
  const byId = new Map(graph.concepts.map((c) => [c.concept_id, c]));
  let path = null;
  let selected = null;
  let seq = 0;
  let showAll = false;
  let shown = graph;
  let layout = layoutGraph(graph);

  // 논문 위치는 페이지와 '찾을 말'로 안내한다. 사용자는 자기 PDF에서 Ctrl+F(⌘F)로 찾는다.
  const pages = (locations) => {
    const phrase = locations[0]?.search_phrase;
    return html`PDF 기준 ${locations.map((l) => l.pdf_page).join(', ')}페이지${phrase ? html` · 찾을 말 <q class="kg-phrase">${phrase}</q> <button type="button" class="link-btn kg-copy" data-copy="${phrase}" aria-label="찾을 말 ${phrase} 복사">복사</button>` : ''}`;
  };

  setHTML(view, html`
    <div class="page-head">
      <div>
        <h1 class="page-title">${graph.label}</h1>
        <p class="page-desc">읽는 목적에 맞춰 먼저 알아야 할 개념과 순서를 보여줘요. 선수 관계는 팀이 검수한 교육적 판단이에요.</p>
      </div>
      ${source ? html`<a class="btn sm" href="${source}" target="_blank" rel="noopener noreferrer">원문 PDF</a>` : ''}
    </div>
    <form class="card kg-controls" data-role="controls">
      <div class="kg-control" role="group" aria-label="읽는 목적">
        <span class="kg-label">읽는 목적</span>
        <div class="kg-chips">${GOALS.map(([v, label]) => html`<button type="button" class="chip" data-goal="${v}" aria-pressed="${state.goal === v}">${label}</button>`)}</div>
      </div>
      <div class="kg-control" role="group" aria-label="표시 범위">
        <span class="kg-label">범위</span>
        <div class="kg-chips">${MODES.map(([v, label]) => html`<button type="button" class="chip" data-mode="${v}" aria-pressed="${state.mode === v}">${label}</button>`)}</div>
      </div>
      <label class="kg-control kg-known">
        <span class="kg-label">이미 아는 개념</span>
        <input class="input" name="known" type="text" maxlength="1000" placeholder="예: 소프트맥스, 내적 (쉼표로 구분)" value="${state.known}">
      </label>
      <button type="submit" class="btn btn-primary sm">경로 다시 계산</button>
    </form>
    <div class="kg-body">
      <section class="card kg-steps" aria-labelledby="kg-steps-title">
        <h2 id="kg-steps-title" class="kg-section-title">학습 순서</h2>
        <div data-role="steps">${loadingBlock('경로를 계산하는 중이에요')}</div>
      </section>
      <section class="card kg-map" aria-labelledby="kg-map-title">
        <div class="kg-map-head">
          <h2 id="kg-map-title" class="kg-section-title">개념 관계</h2>
          <button type="button" class="chip sm" data-action="toggle-all" aria-pressed="false">전체 개념 보기</button>
          <p class="kg-legend"><span class="kg-dot target"></span>목표 <span class="kg-dot required"></span>필수 선수 <span class="kg-dot optional"></span>선택 <span class="kg-dot skipped"></span>건너뜀 <span class="kg-dot idle"></span>이번 경로 밖</p>
        </div>
        <div class="kg-canvas" data-role="canvas"></div>
        <div class="kg-detail" data-role="detail" aria-live="polite"></div>
      </section>
    </div>
  `);

  const stepsEl = view.querySelector('[data-role="steps"]');
  const canvasEl = view.querySelector('[data-role="canvas"]');
  const detailEl = view.querySelector('[data-role="detail"]');

  function roleOf(id) {
    if (!path) return 'idle';
    const step = path.steps.find((s) => s.concept_id === id);
    if (step) return step.role;
    return path.skipped.some((s) => s.concept_id === id) ? 'skipped' : 'idle';
  }

  function renderSteps() {
    if (!path.steps.length) {
      setHTML(stepsEl, emptyBlock('더 배울 선수 개념이 없어요.', '목표 개념을 이미 안다고 표시했어요.'));
    } else {
      setHTML(stepsEl, html`<ol class="kg-step-list">${path.steps.map((s) => html`
        <li class="kg-step ${s.role}">
          <button type="button" class="kg-step-btn" data-concept="${s.concept_id}">
            <span class="kg-step-no">${s.order}</span>
            <span class="kg-step-main">
              <span class="kg-step-name">${s.name_ko} <span class="hint">${s.name_en}</span></span>
              <span class="kg-step-reason">${s.reason_ko}</span>
            </span>
            <span class="badge ${s.role === 'target' ? 'b-reading' : s.role === 'optional' ? 'b-gray' : 'b-ok'}">${ROLE_LABEL[s.role]}</span>
          </button>
          ${s.paper_locations.length ? html`<span class="kg-pages">${pages(s.paper_locations)}</span>` : ''}
        </li>`)}</ol>`);
    }
    const notes = [];
    if (path.skipped.length) {
      notes.push(html`<p class="hint">건너뜀: ${path.skipped.map((s) => `${s.name_ko}(${SKIP_LABEL[s.reason]})`).join(', ')}</p>`);
    }
    if (path.unresolved_concepts.length) {
      notes.push(html`<p class="hint">지도에 없는 개념이라 반영하지 않았어요: ${path.unresolved_concepts.join(', ')}</p>`);
    }
    if (notes.length) stepsEl.insertAdjacentHTML('beforeend', notes.map(String).join(''));
  }

  function renderMap() {
    canvasEl.innerHTML = drawSvg(shown, layout, roleOf, selected);
  }

  // 기본은 이번 경로의 개념만, '전체 개념 보기'를 누르면 논문의 모든 개념을 그린다.
  function relayout() {
    const next = showAll ? graph : focusGraph(graph, path);
    if (next !== shown) {
      shown = next;
      layout = layoutGraph(shown);
    }
    renderMap();
  }

  // 그래프가 화면보다 넓으면 첫 목표 개념이 보이도록 가로 스크롤을 맞춘다.
  function revealTarget() {
    const target = path?.steps.find((s) => s.role === 'target');
    const point = target && layout.pos.get(target.concept_id);
    const svg = canvasEl.querySelector('svg');
    if (!point || !svg || canvasEl.scrollWidth <= canvasEl.clientWidth) return;
    const scale = svg.getBoundingClientRect().width / layout.width;
    canvasEl.scrollLeft = Math.max(0, (point.x + NODE_W / 2) * scale - canvasEl.clientWidth / 2);
  }

  function renderDetail() {
    if (!selected) {
      setHTML(detailEl, html`<p class="hint">개념을 누르면 설명과 선수 관계를 볼 수 있어요.</p>`);
      return;
    }
    const c = byId.get(selected);
    const needs = graph.prerequisites.filter((e) => e.concept_id === selected);
    const usedBy = graph.prerequisites.filter((e) => e.prerequisite_id === selected);
    setHTML(detailEl, html`
      <h3 class="kg-detail-title">${c.name_ko} <span class="hint">${c.name_en}</span></h3>
      <p>${c.description_ko}</p>
      ${c.paper_locations.length ? html`<p class="hint">이 논문의 ${pages(c.paper_locations)}${c.paper_locations[0].section_hint ? ` · ${c.paper_locations[0].section_hint}` : ''}</p>` : html`<p class="hint">논문에 직접 나오지 않는 기초 개념이에요.</p>`}
      ${needs.length ? html`<p class="kg-detail-label">먼저 알면 좋은 개념</p><ul class="kg-detail-list">${needs.map((e) => html`<li><button type="button" class="link-btn" data-concept="${e.prerequisite_id}">${byId.get(e.prerequisite_id).name_ko}</button> — ${e.rationale_ko}</li>`)}</ul>` : ''}
      ${usedBy.length ? html`<p class="kg-detail-label">이 개념이 필요한 개념</p><p>${usedBy.map((e, i) => html`${i ? ', ' : ''}<button type="button" class="link-btn" data-concept="${e.concept_id}">${byId.get(e.concept_id).name_ko}</button>`)}</p>` : ''}
    `);
  }

  async function loadPath() {
    const mine = ++seq;
    path = null;
    renderMap();
    setHTML(stepsEl, loadingBlock('경로를 계산하는 중이에요'));
    try {
      const result = await api.getConceptPath(paperKey, state);
      if (mine !== seq) return;
      path = result;
      renderSteps();
      relayout();
      revealTarget();
    } catch (error) {
      if (mine !== seq) return;
      setHTML(stepsEl, errorBlock(error, 'reload-path'));
    }
  }

  function select(id) {
    if (!byId.has(id)) return;
    selected = id;
    renderMap();
    renderDetail();
  }

  view.addEventListener('click', async (e) => {
    const copy = e.target.closest('[data-copy]');
    if (copy) {
      const ok = await copyText(copy.dataset.copy);
      toast(ok ? '복사했어요. 내 PDF에서 Ctrl+F(Mac은 ⌘F)로 찾아보세요.' : '복사하지 못했어요. 직접 선택해서 복사해 주세요.');
      return;
    }
    const goal = e.target.closest('[data-goal]');
    const mode = e.target.closest('[data-mode]');
    const concept = e.target.closest('[data-concept]');
    if (goal || mode) {
      if (goal) state.goal = goal.dataset.goal;
      if (mode) state.mode = mode.dataset.mode;
      view.querySelectorAll('[data-goal]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.goal === state.goal)));
      view.querySelectorAll('[data-mode]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === state.mode)));
      replaceQuery(state);
      loadPath();
    } else if (concept) {
      select(concept.dataset.concept);
    } else if (e.target.closest('[data-action="reload-path"]')) {
      loadPath();
    } else if (e.target.closest('[data-action="toggle-all"]')) {
      showAll = !showAll;
      e.target.closest('[data-action="toggle-all"]').setAttribute('aria-pressed', String(showAll));
      relayout();
      revealTarget();
    }
  });
  view.addEventListener('keydown', (e) => {
    const node = e.target.closest('g[data-concept]');
    if (node && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      select(node.dataset.concept);
    }
  });
  view.querySelector('[data-role="controls"]').addEventListener('submit', (e) => {
    e.preventDefault();
    state.known = e.target.elements.known.value.trim();
    replaceQuery(state);
    loadPath();
  });

  renderDetail();
  await loadPath();
}

/**
 * 선수 → 개념 방향으로 왼쪽에서 오른쪽으로 층을 나눈다.
 * 층은 가장 긴 선수 사슬의 길이, 층 안의 순서는 선수들의 평균 위치(한 번 정렬)로 교차를 줄인다.
 */
function layoutGraph(graph) {
  const ids = graph.concepts.map((c) => c.concept_id);
  const parents = new Map(ids.map((id) => [id, []]));
  graph.prerequisites.forEach((e) => parents.get(e.concept_id)?.push(e.prerequisite_id));
  const depth = new Map();
  const depthOf = (id, seen = new Set()) => {
    if (depth.has(id)) return depth.get(id);
    if (seen.has(id)) return 0; // 서버가 순환을 거부하지만 화면은 멈추지 않게 한다.
    seen.add(id);
    const d = Math.max(-1, ...parents.get(id).map((p) => depthOf(p, seen))) + 1;
    depth.set(id, d);
    return d;
  };
  ids.forEach((id) => depthOf(id));
  const columns = [];
  ids.forEach((id) => { (columns[depth.get(id)] ??= []).push(id); });
  const children = new Map(ids.map((id) => [id, []]));
  graph.prerequisites.forEach((e) => children.get(e.prerequisite_id)?.push(e.concept_id));
  const row = new Map();
  const place = (col) => col.forEach((id, i) => row.set(id, i));
  const average = (list, fallback) => {
    const values = list.map((v) => row.get(v)).filter((v) => v !== undefined);
    return values.length ? values.reduce((a, b) => a + b, 0) / values.length : fallback;
  };
  columns.forEach((col) => { col.sort(); place(col); });
  // 선수 기준 정렬과 후속 기준 정렬을 몇 번 번갈아 해 교차를 줄인다(결과는 항상 같다).
  for (let sweep = 0; sweep < 4; sweep += 1) {
    for (let ci = 1; ci < columns.length; ci += 1) {
      const col = columns[ci];
      const key = new Map(col.map((id) => [id, average(parents.get(id), row.get(id))]));
      col.sort((a, b) => key.get(a) - key.get(b) || a.localeCompare(b));
      place(col);
    }
    for (let ci = columns.length - 2; ci >= 0; ci -= 1) {
      const col = columns[ci];
      const key = new Map(col.map((id) => [id, average(children.get(id), row.get(id))]));
      col.sort((a, b) => key.get(a) - key.get(b) || a.localeCompare(b));
      place(col);
    }
  }
  const tallest = Math.max(...columns.map((c) => c.length));
  const height = PAD * 2 + tallest * NODE_H + (tallest - 1) * ROW_GAP;
  const pos = new Map();
  columns.forEach((col, ci) => {
    const colHeight = col.length * NODE_H + (col.length - 1) * ROW_GAP;
    const top = (height - colHeight) / 2;
    col.forEach((id, ri) => pos.set(id, { x: PAD + ci * (NODE_W + COL_GAP), y: top + ri * (NODE_H + ROW_GAP) }));
  });
  return { pos, width: PAD * 2 + columns.length * NODE_W + (columns.length - 1) * COL_GAP, height };
}

function clip(text, max) {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function drawSvg(graph, layout, roleOf, selected) {
  const edges = graph.prerequisites.map((e) => {
    const a = layout.pos.get(e.prerequisite_id);
    const b = layout.pos.get(e.concept_id);
    if (!a || !b) return '';
    const x1 = a.x + NODE_W;
    const y1 = a.y + NODE_H / 2;
    const x2 = b.x;
    const y2 = b.y + NODE_H / 2;
    const mid = (x1 + x2) / 2;
    const active = roleOf(e.prerequisite_id) !== 'idle' && roleOf(e.prerequisite_id) !== 'skipped'
      && roleOf(e.concept_id) !== 'idle' && roleOf(e.concept_id) !== 'skipped';
    return `<path class="kg-edge${active ? ' on' : ''}" d="M${x1} ${y1}C${mid} ${y1} ${mid} ${y2} ${x2 - 4} ${y2}" marker-end="url(#kg-arrow${active ? '-on' : ''})"/>`;
  }).join('');
  const nodes = graph.concepts.map((c) => {
    const p = layout.pos.get(c.concept_id);
    const role = roleOf(c.concept_id);
    const label = `${c.name_ko}${c.in_paper ? '' : ' (기초)'}`;
    return `<g class="kg-node ${role}${selected === c.concept_id ? ' selected' : ''}" data-concept="${esc(c.concept_id)}" tabindex="0" role="button" aria-label="${esc(`${label}, ${ROLE_LABEL[role] ?? (role === 'skipped' ? '건너뜀' : '경로 밖')}`)}" transform="translate(${p.x} ${p.y})">`
      + `<rect width="${NODE_W}" height="${NODE_H}" rx="10"/>`
      + `<text x="${NODE_W / 2}" y="${c.in_paper ? 20 : 27}" text-anchor="middle" class="kg-node-name">${esc(clip(c.name_ko, 12))}</text>`
      + (c.in_paper ? `<text x="${NODE_W / 2}" y="36" text-anchor="middle" class="kg-node-sub">${esc(clip(c.name_en, 22))}</text>` : '')
      + '</g>';
  }).join('');
  return `<svg class="kg-svg" style="min-width:${Math.round(layout.width * 0.72)}px;max-width:${layout.width}px" viewBox="0 0 ${layout.width} ${layout.height}" role="img" aria-label="개념 선수 관계 지도">`
    + '<defs><marker id="kg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="kg-arrow"/></marker>'
    + '<marker id="kg-arrow-on" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="kg-arrow on"/></marker></defs>'
    + edges + nodes + '</svg>';
}

export { layoutGraph, sourceLink, focusGraph };
