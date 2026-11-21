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

/** 화면 종류(로그인처럼 메뉴 없는 화면인지)를 반영하고, 첫 로딩 때 숨겨 둔 화면을 보여준다. */
function showShell(route) {
  document.body.classList.toggle('bare', !!route.bare);
  setActiveNav(route.nav);
  updateBackButton(route);
  document.body.classList.remove('booting');
}

async function renderRoute() {
  const seq = ++renderSeq;
  stampHistory();
  const { path, query } = parseHash();
  const matched = matchRoute(path);
  if (!matched) { navigate('/papers', undefined, { replace: true }); return; }
  const { route, params } = matched;

  // 로그인이 필요한 화면은 세션부터 확인한다. 확인 전에 메뉴·로딩 화면을 그리면
  // 로그인하지 않은 사용자에게 논문 화면이 잠깐 보였다가 로그인 화면으로 넘어간다.
  if (!route.public) {
    let signedIn;
    try {
      signedIn = await ensureSession();
    } catch (error) {
      if (seq !== renderSeq) return;
      currentRoute = route;
      showShell(route);
      console.error(error);
      document.title = '불러오지 못했어요 · Solo Leveling';
      const view = document.getElementById('view');
      setHTML(view, errorBlock(error));
      return;
    }
    if (seq !== renderSeq) return;
    if (!signedIn) {
      navigate('/login', undefined, { replace: true });
      return;
    }
  }
  currentRoute = route;
  showShell(route);

  // 화면마다 새 컨테이너를 써서 이전 화면의 이벤트가 남지 않게 한다.
  const old = document.getElementById('view');
  const view = old.cloneNode(false);
  old.replaceWith(view);
  setHTML(view, loadingBlock());

  const searchInput = document.getElementById('global-q');
  searchInput.value = path === '/search' ? (query.q ?? '') : '';

  try {
    const module = await route.load();
    if (seq !== renderSeq) return;
    await module.default({ view, params, query });
    if (seq === renderSeq && document.activeElement === document.body) view.focus({ preventScroll: true });
  } catch (error) {
    if (seq !== renderSeq) return;
    if (error instanceof ApiError && error.status === 401) return; // auth:expired 처리로 이동
    console.error(error);
    // 화면을 그리기 전에 실패하면 탭 제목이 이전 화면 것으로 남으므로 여기서 바꾼다.
    document.title = '불러오지 못했어요 · Solo Leveling';
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

// ---------- ... 메뉴 ----------
// 메뉴는 열 때 버튼 위치를 재서 화면 기준(position: fixed)으로 띄운다.
// 그래야 가로 스크롤 때문에 overflow가 걸린 표 카드 안에서도 메뉴가 잘리지 않는다.
// 아래 공간이 모자라면 버튼 위로 펼친다.
const MENU_GAP = 2;
const MENU_MARGIN = 8;

function placeMenu(details) {
  const trigger = details.querySelector(':scope > summary');
  const menu = details.querySelector(':scope > .menu');
  if (!trigger || !menu) return;
  menu.classList.add('floating');
  const t = trigger.getBoundingClientRect();
  const m = menu.getBoundingClientRect();
  const left = Math.max(MENU_MARGIN, Math.min(t.right - m.width, window.innerWidth - m.width - MENU_MARGIN));
  const below = t.bottom + MENU_GAP;
  const above = t.top - MENU_GAP - m.height;
  const fitsBelow = below + m.height <= window.innerHeight - MENU_MARGIN;
  const top = fitsBelow || above < MENU_MARGIN ? below : above;
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
}

function closeMenus() {
  document.querySelectorAll('details.menu-wrap[open]').forEach((d) => d.removeAttribute('open'));
}

// summary의 기본 열기 동작 대신 직접 열고 같은 순간에 위치를 잡아, 제자리에 잠깐 그려지는 깜빡임을 막는다.
document.addEventListener('click', (e) => {
  const trigger = e.target.closest('details.menu-wrap > summary');
  if (!trigger || trigger.parentElement.open) return;
  e.preventDefault();
  trigger.parentElement.open = true;
  placeMenu(trigger.parentElement);
});
// 화면 기준으로 띄운 메뉴는 스크롤을 따라가지 않으므로, 스크롤하거나 창 크기가 바뀌면 닫는다.
document.addEventListener('scroll', closeMenus, true);
window.addEventListener('resize', closeMenus);

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
