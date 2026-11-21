// 첫 로그인 동의 — 항목별로 한 번 받고 이후 업로드·메모·퀴즈·요약 저장에서는 다시 묻지 않는다.
import { api } from '../api/index.js';
import { html, setHTML } from '../lib/html.js';
import { navigate } from '../router.js';
import { errorMessage } from '../lib/ui.js';
import { announceConsents, checkedConsents, consentItem } from '../lib/account-consents.js';

export default async function consentPage({ view, query }) {
  document.title = '서비스 이용 동의 · Solo Leveling';
  const returnTo = typeof query.return_to === 'string' && query.return_to.startsWith('/')
    && !query.return_to.startsWith('//') && query.return_to !== '/consent' ? query.return_to : '/papers';
  let status = checkedConsents(await api.getAccountConsents());
  let busy = false, message = '';
  const checked = Object.fromEntries(status.items.map((item) => [item.purpose, item.state === 'current']));

  function draw() {
    if (!view.isConnected) return;
    const all = status.items.every((item) => checked[item.purpose]);
    const ready = status.items.every((item) => !item.required || checked[item.purpose]);
    setHTML(view, html`
      <h1 class="page-title">서비스 이용 동의</h1>
      <section class="card panel" aria-labelledby="consent-h">
        <h2 id="consent-h" class="panel-title">처음 한 번만 확인해 주세요</h2>
        <p>여기서 동의하면 PDF 업로드, 메모·하이라이트, 읽기 질문 답변, 퀴즈 기록, 요약을 저장할 때마다
        다시 묻지 않아요. 계정 화면에서 항목별로 언제든 바꿀 수 있어요.</p>
        <label class="consent-all"><input type="checkbox" data-all ${all ? 'checked' : ''} ${busy ? 'disabled' : ''}>
          <strong>전체 동의</strong> (선택 항목 포함)</label>
        <div class="stack">
          ${status.items.map((item) => consentItem(item, html`<label><input type="checkbox" data-purpose="${item.purpose}"
            ${checked[item.purpose] ? 'checked' : ''} ${busy ? 'disabled' : ''}>
            ${item.required ? '[필수] 위 내용에 동의해요.' : '[선택] 위 내용에 동의해요.'}</label>`))}
        </div>
        <p class="hint">선택 항목에 동의하지 않아도 가입하고 PDF를 보관할 수 있어요. 다만 업로드한 PDF의 학습 자료 생성과
        서버 생성 기능은 쓸 수 없어요. 서버 생성 비용은 운영자가 부담해요.</p>
        <p role="status" aria-live="polite">${message}</p>
        <div class="actions">
          <button type="button" class="btn btn-primary" data-submit ${!ready || busy ? 'disabled' : ''}>동의하고 시작하기</button>
        </div>
      </section>`);
  }

  view.addEventListener('change', (event) => {
    const target = event.target;
    if (target.matches('[data-all]')) {
      for (const item of status.items) checked[item.purpose] = target.checked;
    } else if (target.matches('[data-purpose]')) {
      checked[target.dataset.purpose] = target.checked;
    } else return;
    draw();
  });
  view.addEventListener('click', async (event) => {
    if (!event.target.closest('[data-submit]') || busy) return;
    const purposes = status.items.filter((item) => checked[item.purpose]).map((item) => item.purpose);
    busy = true; message = '동의를 저장하고 있어요.'; draw();
    try {
      status = checkedConsents(await api.grantAccountConsents(purposes, status.notice_version));
      announceConsents(status);
      if (status.complete) {
        navigate(returnTo, undefined, { replace: true });
        return;
      }
      message = '필수 항목 동의를 저장하지 못했어요. 다시 시도해 주세요.';
    } catch (error) {
      if (error?.status === 409) {
        status = checkedConsents(await api.getAccountConsents());
        message = '안내 내용이 바뀌었어요. 다시 읽고 동의해 주세요.';
      } else {
        message = errorMessage(error);
      }
    } finally {
      busy = false;
      draw();
    }
  });
  draw();
}
