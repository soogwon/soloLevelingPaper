// MCP 설치 가이드: 원격/로컬 탭, 운영체제 선택, 명령 복사

(() => {
  const tabs = [...document.querySelectorAll('.tab')];
  const panels = { remote: document.getElementById('remote'), local: document.getElementById('local') };

  function selectTab(name, { focus = false, scroll = false } = {}) {
    if (!panels[name]) return;
    tabs.forEach((tab) => {
      const on = tab.dataset.tab === name;
      tab.setAttribute('aria-selected', String(on));
      tab.tabIndex = on ? 0 : -1;
      if (on && focus) tab.focus();
    });
    Object.entries(panels).forEach(([key, panel]) => { panel.hidden = key !== name; });
    if (scroll) document.getElementById('guide').scrollIntoView({ block: 'start' });
  }

  tabs.forEach((tab, i) => {
    tab.addEventListener('click', () => {
      selectTab(tab.dataset.tab);
      history.replaceState(null, '', `#${tab.dataset.tab}`);
    });
    // 왼쪽·오른쪽 화살표로 탭 이동
    tab.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      const next = tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
      selectTab(next.dataset.tab, { focus: true });
    });
  });

  // 메뉴·버튼의 #remote, #local 링크는 탭을 바꾸고 설치 방법 구간으로 이동한다
  document.querySelectorAll('[data-tab-link]').forEach((link) => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      selectTab(link.dataset.tabLink, { scroll: true });
      history.replaceState(null, '', `#${link.dataset.tabLink}`);
    });
  });

  const fromHash = location.hash.replace('#', '');
  if (fromHash === 'local' || fromHash === 'remote') selectTab(fromHash, { scroll: true });

  // 운영체제 선택 (Windows에서 열면 Windows를 먼저 보여 준다)
  const osButtons = [...document.querySelectorAll('.os-btn')];
  function selectOs(os) {
    osButtons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.os === os)));
    document.querySelectorAll('.os-only').forEach((el) => { el.hidden = el.dataset.for !== os; });
  }
  osButtons.forEach((b) => b.addEventListener('click', () => selectOs(b.dataset.os)));
  if (/Windows/i.test(navigator.userAgent)) selectOs('win');

  // 복사 버튼
  document.querySelectorAll('.copy').forEach((button) => {
    button.addEventListener('click', async () => {
      const target = document.querySelector(button.dataset.copy);
      if (!target) return;
      const text = target.innerText.trim();
      const label = button.textContent;
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = '복사됨';
      } catch {
        // 클립보드를 쓸 수 없으면 직접 복사할 수 있게 선택해 둔다
        const range = document.createRange();
        range.selectNodeContents(target);
        const sel = getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        button.textContent = '선택됨';
      }
      setTimeout(() => { button.textContent = label; }, 1500);
    });
  });
})();
