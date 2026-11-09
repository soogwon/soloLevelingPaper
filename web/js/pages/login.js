// 7.2 웹 로그인 — GET /v1/auth/login 으로 이동 → 인증 후 /v1/auth/callback 이 세션을 만들고 되돌려 보낸다.
import { CONFIG } from '../config.js';
import { api } from '../api/index.js';
import { html, setHTML } from '../lib/html.js';
import { navigate } from '../router.js';
import { errorMessage } from '../lib/ui.js';

export default async function loginPage({ view, query }) {
  document.title = '로그인 · Solo Leveling';
  setHTML(view, html`<div class="login-wrap">
    <div class="brand-name">Solo Leveling<span>.</span></div>
    <section class="card login-card" aria-labelledby="login-h">
      <span class="badge b-reading" style="align-self:flex-start">웹 로그인</span>
      <h1 id="login-h">학습 기록을 어디서든 다시 보세요</h1>
      <p class="meta">Claude에서 공부한 논문, 마지막 위치, 메모를 모아 볼 수 있어요.</p>
      ${query.expired ? html`<p class="alert" role="alert">로그인이 만료됐어요. 다시 로그인해 주세요.</p>` : ''}
      <button type="button" class="btn btn-block" data-action="login">Google 계정으로 계속하기</button>
      <p class="inline-error" data-slot="error" role="alert" hidden></p>
      <p class="hint">Claude 계정과는 별개의 Solo Leveling 계정이에요. 처음 로그인하면 계정이 만들어져요.</p>
      ${CONFIG.USE_MOCK ? html`<p class="hint">지금은 예시 데이터로 실행 중이라 버튼을 누르면 바로 로그인돼요.</p>` : ''}
    </section>
  </div>`);

  view.querySelector('[data-action="login"]').addEventListener('click', async (e) => {
    if (!CONFIG.USE_MOCK) {
      // 로그인 후 돌아올 화면. 허용된 복귀 주소인지는 서버가 검사한다.
      const returnTo = `${window.location.origin}${window.location.pathname}#/papers`;
      window.location.assign(api.loginUrl(returnTo));
      return;
    }
    const button = e.currentTarget;
    button.disabled = true;
    try {
      await api.mockLogin();
      navigate('/papers', undefined, { replace: true });
    } catch (error) {
      const el = view.querySelector('[data-slot="error"]');
      el.hidden = false;
      el.textContent = errorMessage(error);
      button.disabled = false;
    }
  });
}
