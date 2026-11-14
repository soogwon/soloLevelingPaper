// 해시 라우터: 정적 파일 서버만으로 동작하도록 #/papers 형태의 주소를 쓴다.
// parent: 앱 안에서 이전 화면 기록이 없을 때 '뒤로'가 이동할 화면 (null이면 버튼 비활성화)

const ROUTES = [
  { pattern: /^\/papers$/, nav: 'papers', parent: null, load: () => import('./pages/papers.js') },
  { pattern: /^\/papers\/([^/]+)$/, keys: ['paperId'], nav: 'papers', parent: '/papers', load: () => import('./pages/paper-detail.js') },
  { pattern: /^\/notes$/, nav: 'notes', parent: '/papers', load: () => import('./pages/notes.js') },
  { pattern: /^\/collections$/, nav: 'collections', parent: '/papers', load: () => import('./pages/collections.js') },
  { pattern: /^\/collections\/([^/]+)$/, keys: ['collectionId'], nav: 'collections', parent: '/collections', load: () => import('./pages/collection-detail.js') },
  { pattern: /^\/concepts$/, nav: 'concepts', parent: '/papers', load: () => import('./pages/concepts.js') },
  { pattern: /^\/concepts\/([^/]+)$/, keys: ['paperKey'], nav: 'concepts', parent: '/concepts', load: () => import('./pages/concepts.js') },
  { pattern: /^\/search$/, nav: null, parent: '/papers', load: () => import('./pages/search.js') },
  { pattern: /^\/account$/, nav: 'account', parent: '/papers', load: () => import('./pages/account.js') },
  { pattern: /^\/login$/, nav: null, parent: null, public: true, bare: true, load: () => import('./pages/login.js') },
];

export function parseHash() {
  const raw = window.location.hash.replace(/^#/, '') || '/papers';
  const [path, qs = ''] = raw.split('?');
  return { path, query: Object.fromEntries(new URLSearchParams(qs)) };
}

export function buildHash(path, query = {}) {
  const entries = Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== '');
  const qs = new URLSearchParams(entries).toString();
  return `#${path}${qs ? `?${qs}` : ''}`;
}

/**
 * 화면 이동. replace: true면 현재 기록을 바꿔서 '뒤로'로 돌아오지 않게 한다 (로그인 이동 등).
 */
export function navigate(path, query, { replace = false } = {}) {
  const next = buildHash(path, query);
  if (window.location.hash === next) {
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  } else if (replace) {
    history.replaceState(history.state, '', next); // 기록 순번(idx)은 그대로 둔다

    window.dispatchEvent(new HashChangeEvent('hashchange'));
  } else {
    window.location.hash = next;
  }
}

/**
 * 화면을 다시 그리지 않고 주소의 쿼리만 바꾼다 (필터·페이지 번호 유지용).
 * 기록을 새로 쌓지 않으므로 '뒤로'는 필터 변경이 아니라 이전 화면으로 간다.
 */
export function replaceQuery(query) {
  const { path } = parseHash();
  history.replaceState(history.state, '', buildHash(path, query));
}

export function matchRoute(path) {
  for (const route of ROUTES) {
    const m = route.pattern.exec(path);
    if (m) {
      const params = {};
      (route.keys ?? []).forEach((key, i) => { params[key] = decodeURIComponent(m[i + 1]); });
      return { route, params };
    }
  }
  return null;
}
