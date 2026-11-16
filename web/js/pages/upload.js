import { api } from '../api/index.js';
import { CONFIG } from '../config.js';
import { html, setHTML } from '../lib/html.js';
import { createUploadIntent, uploadMessages } from '../lib/upload-intent.js';
import { createUploadConsents, consentMessages, consentPurposes } from '../lib/upload-consents.js';

export default function uploadPage({ view }) {
  document.title = '새 PDF 등록 준비 · Solo Leveling';
  setHTML(view, html`<h1 class="page-title">새 PDF 등록 준비</h1>
    <section class="card panel">
      <p>파일명·크기·형식·SHA-256 파일 지문을 로그인한 계정의 서버에 보내 등록을 준비해요.
      이 단계에서 PDF 본문을 전송하거나 AI를 호출하지 않아요.</p>
      <label>PDF 선택 <input type="file" accept=".pdf,application/pdf" data-file></label>
      <p class="hint">최대 50 MiB. 파일 재선택·취소 시 이전 요청 표시는 초기화돼요. 서버 기록이 삭제되지는 않아요.</p>
      <button class="btn" type="button" data-clear>선택 취소</button>
      <p data-file-label></p>
      <label><input type="checkbox" data-agree> 파일 정보와 파일 지문을 서버로 보내는 데 동의해요.</label>
      <div><button class="btn" type="button" data-create disabled>등록 준비 저장</button>
      <button class="btn" type="button" data-read disabled>기존 요청 상태 확인</button></div>
      <p role="status" aria-live="polite" data-status></p><p data-intent></p>
      <section aria-label="PDF 보관 및 AI 전송 동의">
        <h2>PDF 보관 및 AI 전송 동의</h2>
        <p>파일 정보 전송 확인과 별개의 동의예요. 서버의 고지를 읽고 목적별로 선택해 주세요.</p>
        <button class="btn" type="button" data-consent-read disabled>동의 안내·상태 조회</button>
        <p data-notice-version></p>
        <p role="status" aria-live="polite" data-consent-status></p>
        ${consentPurposes.map(p => html`<fieldset>
          <legend>${p === 'storage' ? 'PDF 보관 동의' : 'AI 전송 동의'}</legend>
          <p data-notice-${p}></p><p data-consent-${p}></p>
          <label><input type="checkbox" data-approve-${p} disabled>
            ${p === 'storage' ? '위 고지를 읽고 PDF 서버 보관에 동의해요.' : '위 고지를 읽고 OpenAI 전송에 동의해요.'}</label>
          <button class="btn" type="button" data-grant-${p} disabled>동의 저장</button>
          <button class="btn" type="button" data-revoke-${p} disabled>동의 철회</button>
        </fieldset>`)}
        <p>AI 동의는 서버에서 PDF 검증이 완료된 뒤에만 저장할 수 있어요.
        동의 철회는 서버 파일 삭제와 다르며, 이미 전송된 요청을 되돌리지 않아요.</p>
      </section>
      <p>현재는 동의 저장·조회·철회만 지원해요. 파일 업로드·AI 전송·학습 처리는 시작하지 않아요.</p>
      <button class="btn" type="button" disabled>PDF 업로드 (준비 중)</button>
      <p class="hint">새로고침·화면 이동·로그아웃 시 이 화면의 요청 키는 지워져요. 결과가 미확정이면
      화면을 유지하고 같은 요청을 확인하세요. 새로 파일을 선택하면 별도 등록 준비가 생길 수 있어요.</p>
    </section>`);
  const input = view.querySelector('[data-file]'), agree = view.querySelector('[data-agree]');
  const create = view.querySelector('[data-create]'), read = view.querySelector('[data-read]');
  const consents = createUploadConsents({api, changed:drawConsents});
  const controller = createUploadIntent({ api, changed: draw });
  function drawConsents(state, clearChecks = true) {
    if (!view.isConnected) return;
    view.querySelector('[data-consent-read]').disabled = CONFIG.USE_MOCK || !state.canRead;
    view.querySelector('[data-consent-status]').textContent = consentMessages[state.busy ? 'busy' : state.status];
    view.querySelector('[data-notice-version]').textContent = state.notice ? `고지 버전: ${state.notice.version}` : '';
    const labels = {current:'동의됨',stale:'고지 변경 · 재동의 필요',revoked:'철회됨'};
    for (const p of consentPurposes) {
      const check = view.querySelector(`[data-approve-${p}]`);
      if (clearChecks) check.checked = false;
      check.disabled = CONFIG.USE_MOCK || !state.canGrant[p];
      view.querySelector(`[data-notice-${p}]`).textContent = state.notice?.[p] ?? '';
      view.querySelector(`[data-consent-${p}]`).textContent = state.notice
        ? (state.values[p] ? `${labels[state.values[p].state]} · revision ${state.values[p].revision}` : '동의 기록 없음')
        : '동의 상태 미확인';
      view.querySelector(`[data-grant-${p}]`).disabled = check.disabled || !check.checked;
      view.querySelector(`[data-revoke-${p}]`).disabled = CONFIG.USE_MOCK || !state.canRevoke[p];
    }
  }
  function draw(state) {
    if (!view.isConnected) return;
    view.querySelector('[data-file-label]').textContent = state.file ? `${state.file.name} · ${state.file.size} bytes` : '';
    view.querySelector('[data-status]').textContent = CONFIG.USE_MOCK ? '예시 모드에서는 원격 등록 준비를 실행하지 않아요.' : uploadMessages[state.status];
    view.querySelector('[data-intent]').textContent = state.intent ? `업로드 의도 ID: ${state.intent.intent_id}` : '';
    create.disabled = CONFIG.USE_MOCK || !state.canCreate || !agree.checked;
    create.textContent = state.status === 'uncertain' && !state.intent ? '같은 등록 준비 요청 확인' : '등록 준비 저장';
    read.disabled = CONFIG.USE_MOCK || !state.canRead;
    consents.bind(state.intent ? {...state.intent, status:state.status === 'expired' ? 'expired' : state.intent.status} : null);
  }
  const select = () => { agree.checked = false; return controller.select(input.files?.[0] ?? null); };
  input.addEventListener('change', select);
  input.addEventListener('cancel', () => { input.value = ''; select(); });
  view.querySelector('[data-clear]').addEventListener('click', () => { input.value = ''; select(); });
  agree.addEventListener('change', () => draw(controller.snapshot()));
  create.addEventListener('click', () => { if (view.isConnected && !CONFIG.USE_MOCK) return controller.run(agree.checked); });
  read.addEventListener('click', () => { if (view.isConnected && !CONFIG.USE_MOCK) return controller.run(); });
  view.querySelector('[data-consent-read]').addEventListener('click', () => {
    if (view.isConnected && !CONFIG.USE_MOCK) return consents.refresh();
  });
  for (const p of consentPurposes) {
    const check = view.querySelector(`[data-approve-${p}]`);
    check.addEventListener('change', () => drawConsents(consents.snapshot(),false));
    view.querySelector(`[data-grant-${p}]`).addEventListener('click', () => {
      if (view.isConnected && !CONFIG.USE_MOCK) return consents.grant(p,check.checked);
    });
    view.querySelector(`[data-revoke-${p}]`).addEventListener('click', () => {
      if (view.isConnected && !CONFIG.USE_MOCK) return consents.revoke(p);
    });
  }
  const events = ['auth:expired','auth:logout','hashchange','pagehide'];
  function dispose() {
    events.forEach(event => window.removeEventListener(event, dispose));
    input.value = ''; agree.checked = false; controller.close(); consents.close();
  }
  events.forEach(event => window.addEventListener(event, dispose));
  draw(controller.snapshot());
  drawConsents(consents.snapshot());
}
