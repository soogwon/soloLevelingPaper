import { CONFIG } from './config.js';
import { api, ApiError } from './api/index.js';
import { parseHash, matchRoute, navigate } from './router.js';
import { setHTML } from './lib/html.js';
import { loadingBlock, errorBlock } from './lib/ui.js';

export const session = { user: null };

let renderSeq = 0;
let currentRoute = null;

// ---------- 뒤로가기 ----------
// 앱 안에서 이동한 순서를 history.state.idx에 기록한다.
// idx > 0이면 이전에 본 화면이 있으므로 history.back(), 아니면(링크로 바로 들어온 경우) 상위 화면으로 이동한다.
let currentIdx = null;

function stampHistory() {
  const state = history.state;
  if (state && typeof state.idx === 'number') {
    currentIdx = state.idx;
    return;
  }
  currentIdx = currentIdx === null ? 0 : currentIdx + 1;
  history.replaceState({ ...(state ?? {}), idx: currentIdx }, '');
}

function updateBackButton(route) {
  const button = document.getElementById('back-btn');
  const canGoBack = currentIdx > 0 || !!route.parent;
  button.disabled = !canGoBack;
}

document.getElementById('back-btn').addEventListener('click', () => {
  if (currentIdx > 0) history.back();
  else if (currentRoute?.parent) navigate(currentRoute.parent, undefined, { replace: true });
});

// ---------- 화면 전환 ----------
function setActiveNav(nav) {
  document.querySelectorAll('[data-nav]').forEach((el) => {
    if (el.dataset.nav === nav) el.setAttribute('aria-current', 'page');
    else el.removeAttribute('aria-current');
  });
}

function setAvatar(user) {
  const avatar = document.querySelector('.avatar');
  const name = user?.display_name?.trim();
  avatar.textContent = name ? [...name][0] : '나';
}

async function ensureSession() {
  if (session.user) return true;
  try {
    session.user = await api.me();
    setAvatar(session.user);
    return true;
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return false;
    throw error;
  }
}

async function renderRoute() {
  const seq = ++renderSeq;
  stampHistory();
  const { path, query } = parseHash();
  const matched = matchRoute(path);
  if (!matched) { navigate('/papers', undefined, { replace: true }); return; }
  const { route, params } = matched;
  currentRoute = route;

  document.body.classList.toggle('bare', !!route.bare);
  setActiveNav(route.nav);
  updateBackButton(route);

  // 화면마다 새 컨테이너를 써서 이전 화면의 이벤트가 남지 않게 한다.
  const old = document.getElementById('view');
  const view = old.cloneNode(false);
  old.replaceWith(view);
  setHTML(view, loadingBlock());

  const searchInput = document.getElementById('global-q');
  searchInput.value = path === '/search' ? (query.q ?? '') : '';

  try {
    if (!route.public && !(await ensureSession())) {
      navigate('/login', undefined, { replace: true });
      return;
    }
    const module = await route.load();
    if (seq !== renderSeq) return;
    await module.default({ view, params, query });
    if (seq === renderSeq && document.activeElement === document.body) view.focus({ preventScroll: true });
  } catch (error) {
    if (seq !== renderSeq) return;
    if (error instanceof ApiError && error.status === 401) return; // auth:expired 처리로 이동
    console.error(error);
    setHTML(view, errorBlock(error));
  }
}

// 로그인한 상태였을 때만 '만료' 안내를 띄운다. 직접 로그아웃했거나 처음 들어온 경우는 일반 로그인 화면.
window.addEventListener('auth:expired', () => {
  const wasSignedIn = !!session.user;
  session.user = null;
  if (parseHash().path !== '/login') navigate('/login', wasSignedIn ? { expired: '1' } : undefined, { replace: true });
});
window.addEventListener('auth:logout', () => { session.user = null; });

window.addEventListener('hashchange', () => {
  window.scrollTo(0, 0);
  renderRoute();
});

// 전역 클릭 처리: 오류 화면의 '다시 시도', 열린 메뉴 닫기
document.addEventListener('click', (e) => {
  if (e.target.closest('[data-action="reload-route"]')) renderRoute();
  document.querySelectorAll('details.menu-wrap[open]').forEach((d) => {
    if (!d.contains(e.target)) d.removeAttribute('open');
  });
});
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  document.querySelectorAll('details.menu-wrap[open]').forEach((d) => {
    d.removeAttribute('open');
    d.querySelector('summary')?.focus();
  });
});

document.getElementById('global-search').addEventListener('submit', (e) => {
  e.preventDefault();
  const q = new FormData(e.currentTarget).get('q')?.toString().trim();
  if (q) navigate('/search', { q });
});

if (CONFIG.USE_MOCK) document.querySelector('.mock-flag').hidden = false;

renderRoute();
