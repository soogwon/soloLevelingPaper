import { createProcessingStatus } from '../lib/processing-status.js';
import { api } from '../api/index.js';
import { CONFIG } from '../config.js';
import { html, setHTML } from '../lib/html.js';
import { createUploadIntent, uploadMessages } from '../lib/upload-intent.js';
import { createUploadConsents, consentMessages, consentPurposes } from '../lib/upload-consents.js';
import { accountConsentCurrent } from '../lib/account-consents.js';

// 가입 때 저장 동의를 받은 계정은 파일 선택 뒤 버튼 하나로 업로드한다. 목적별 동의는 서버가 대신 기록한다.
const quickMessages = {
  selected: '파일을 선택했어요. 업로드 버튼을 눌러 주세요.',
  created: '업로드를 준비했어요.',
  verified: 'PDF 업로드를 마쳤어요.',
};

// 화면을 떠나도 되는지 상태별로 알려 준다. 업로드 확인 전 이동하면 이 화면의 요청 키가 지워져 같은 요청을 다시 확인할 수 없다.
const stayStatuses = ['hashing','checking','created','uploading','preparing','sending','completing','consentRequired'];
export function leaveHint({status, busy, quick, withAi}) {
  if (status === 'verified') {
    return {tone:'notice', text: quick && withAi
      ? '업로드가 끝났어요. 이제 다른 페이지로 이동하거나 창을 닫아도 돼요. 학습 자료 만들기는 서버에서 계속 진행되고, 논문 아카이브에서 이 논문을 열면 ‘처리 작업’에서 진행 상황을 볼 수 있어요.'
      : '업로드가 끝났어요. 다른 페이지로 이동해도 서버에 보관된 PDF는 그대로 남아요.'};
  }
  if (['uncertain','uploadUncertain'].includes(status)) {
    return {tone:'alert', text:'업로드 결과를 아직 확인하지 못했어요. 이 화면을 떠나지 말고 위 버튼으로 결과를 다시 확인해 주세요. 새로고침하거나 다른 페이지로 이동하면 이 요청을 다시 확인할 수 없어요.'};
  }
  if (busy || stayStatuses.includes(status)) {
    return {tone:'alert', text:'아직 업로드가 끝나지 않았어요. ‘업로드를 마쳤어요’가 보일 때까지 이 화면에 머물러 주세요. 새로고침하거나 다른 페이지로 이동하면 진행 중인 업로드를 이어서 확인할 수 없어요.'};
  }
  return {tone:'hint', text:'업로드 중에는 이 화면을 떠나지 마세요. ‘업로드를 마쳤어요’가 보이면 이동해도 돼요.'};
}

export default function uploadPage({ view }) {
  const quick = !CONFIG.USE_MOCK && accountConsentCurrent('storage');
  const withAi = quick && accountConsentCurrent('ai_provider');
  const hidden = quick ? 'hidden' : '';
  document.title = (quick ? '새 PDF 업로드' : '새 PDF 등록 준비') + ' · Solo Leveling';
  setHTML(view, html`<h1 class="page-title">${quick ? '새 PDF 업로드' : '새 PDF 등록 준비'}</h1>
    <section class="card panel">
      ${quick ? html`<p>PDF를 고르고 업로드를 누르면 서버에 보관하고 ${withAi
        ? '학습 자료(번역·검색 색인) 만들기를 바로 시작해요.'
        : '보관만 해요. 학습 자료를 만들려면 계정 화면에서 OpenAI 전송에 동의해 주세요.'}
        가입 때 동의한 내용으로 처리하며 <a href="#/account">계정 화면</a>에서 바꿀 수 있어요.</p>`
      : html`<p>파일명·크기·형식·SHA-256 파일 지문을 로그인한 계정의 서버에 보내 등록을 준비해요.
      이 단계에서 PDF 본문을 전송하거나 AI를 호출하지 않아요.</p>`}
      <label>PDF 선택 <input type="file" accept=".pdf,application/pdf" data-file></label>
      <p class="hint">최대 50 MiB. 파일 재선택·취소 시 이전 요청 표시는 초기화돼요. 서버 기록이 삭제되지는 않아요.</p>
      <button class="btn" type="button" data-clear>선택 취소</button>
      <p data-file-label></p>
      <label ${hidden}><input type="checkbox" data-agree> 파일 정보와 파일 지문을 서버로 보내는 데 동의해요.</label>
      <div ${hidden}><button class="btn" type="button" data-create disabled>등록 준비 저장</button>
      <button class="btn" type="button" data-read disabled>기존 요청 상태 확인</button></div>
      ${quick ? html`<div class="actions"><button class="btn btn-primary" type="button" data-quick disabled>업로드</button></div>` : ''}
      <p role="status" aria-live="polite" data-status></p><p data-intent></p>
      <p class="hint" data-leave-hint></p>
      <section aria-label="PDF 보관 및 AI 전송 동의" ${hidden}>
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
      <p ${hidden}>보관 동의 후 아래 버튼을 누르면 PDF를 서버 저장소로 전송하고 검증해요. AI 전송·학습 처리는 시작하지 않아요.</p>
      <button class="btn" type="button" ${hidden} data-upload disabled>PDF 업로드</button>
      <section aria-label="기존 처리 작업 상태">
        <h2>기존 처리 작업 상태</h2>
        <button class="btn" type="button" data-processing-read disabled>처리 상태 조회</button>
        <p role="status" aria-live="polite" data-processing-status></p>
        <p data-processing-job></p>
      </section>
    </section>`);
  const input = view.querySelector('[data-file]'), agree = view.querySelector('[data-agree]');
  const create = view.querySelector('[data-create]'), read = view.querySelector('[data-read]');
  const processing = createProcessingStatus({api, changed:drawProcessing});
  function drawProcessing(state) {
    if (!view.isConnected) return;
    view.querySelector('[data-processing-read]').disabled = CONFIG.USE_MOCK || !state.canRead;
    view.querySelector('[data-processing-status]').textContent = state.message;
    view.querySelector('[data-processing-job]').textContent = state.job ? `작업 ID: ${state.job.job_id}` : '';
  }
  const consents = createUploadConsents({api, changed:drawConsents});
  const controller = createUploadIntent({ api, changed: draw });
  function drawLeaveHint() {
    const node = view.querySelector('[data-leave-hint]');
    if (CONFIG.USE_MOCK) { node.textContent = ''; return; }
    const state = controller.snapshot();
    const hint = leaveHint({status:state.status, busy:state.busy || quickBusy, quick, withAi});
    node.className = hint.tone;
    node.textContent = hint.text;
  }
  function drawUpload() {
    drawLeaveHint();
    const state = controller.snapshot(), consent = consents.snapshot();
    const button = view.querySelector('[data-upload]');
    button.disabled = CONFIG.USE_MOCK || !state.canUpload || consent.busy || consent.status !== 'ready'
      || consent.values.storage?.state !== 'current';
    button.textContent = state.putAttempted ? '업로드 결과 확인 (재전송 없음)' : 'PDF 업로드';
    if (quick) {
      const go = view.querySelector('[data-quick]');
      go.disabled = state.busy || consent.busy || quickBusy || !(state.canCreate || state.canUpload);
      go.textContent = state.putAttempted ? '업로드 결과 확인 (재전송 없음)' : '업로드';
    }
  }
  function drawConsents(state, clearChecks = true) {
    if (!view.isConnected) return;
    view.querySelector('[data-consent-read]').disabled = CONFIG.USE_MOCK || controller.snapshot().busy || !state.canRead;
    view.querySelector('[data-consent-status]').textContent = consentMessages[state.busy ? 'busy' : state.status];
    view.querySelector('[data-notice-version]').textContent = state.notice ? `고지 버전: ${state.notice.version}` : '';
    const labels = {current:'동의됨',stale:'고지 변경 · 재동의 필요',revoked:'철회됨'};
    for (const p of consentPurposes) {
      const check = view.querySelector(`[data-approve-${p}]`);
      if (clearChecks) check.checked = false;
      check.disabled = CONFIG.USE_MOCK || controller.snapshot().busy || !state.canGrant[p];
      view.querySelector(`[data-notice-${p}]`).textContent = state.notice?.[p] ?? '';
      view.querySelector(`[data-consent-${p}]`).textContent = state.notice
        ? (state.values[p] ? `${labels[state.values[p].state]} · revision ${state.values[p].revision}` : '동의 기록 없음')
        : '동의 상태 미확인';
      view.querySelector(`[data-grant-${p}]`).disabled = check.disabled || !check.checked;
      view.querySelector(`[data-revoke-${p}]`).disabled = CONFIG.USE_MOCK || controller.snapshot().busy || !state.canRevoke[p];
    }
    drawUpload();
  }
  function draw(state) {
    if (!view.isConnected) return;
    view.querySelector('[data-file-label]').textContent = state.file ? `${state.file.name} · ${state.file.size} bytes` : '';
    view.querySelector('[data-status]').textContent = CONFIG.USE_MOCK ? '예시 모드에서는 원격 등록 준비를 실행하지 않아요.'
      : quick && state.status === 'verified' ? quickMessages.verified + (withAi
        ? ' 학습 자료 만들기를 접수했어요. 아래에서 처리 상태를 확인할 수 있어요.'
        : ' OpenAI 전송 동의가 없어 학습 자료는 만들지 않았어요.')
      : (quick && quickMessages[state.status]) || uploadMessages[state.status];
    view.querySelector('[data-intent]').textContent = state.intent ? `업로드 의도 ID: ${state.intent.intent_id}` : '';
    drawLeaveHint();
    create.disabled = CONFIG.USE_MOCK || !state.canCreate || !agree.checked;
    create.textContent = state.status === 'uncertain' && !state.intent ? '같은 등록 준비 요청 확인' : '등록 준비 저장';
    read.disabled = CONFIG.USE_MOCK || !state.canRead;
    processing.bind(state.intent);
    consents.bind(state.intent ? {...state.intent, status:state.status === 'expired' ? 'expired' : state.intent.status} : null);
    drawConsents(consents.snapshot());
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
  view.querySelector('[data-upload]').addEventListener('click', () => {
    if (view.isConnected && !CONFIG.USE_MOCK && !view.querySelector('[data-upload]').disabled) {
      return controller.upload(consents.snapshot().values.storage);
    }
  });
  let quickBusy = false;
  async function quickUpload() {
    if (quickBusy) return;
    quickBusy = true; drawUpload();
    try {
      if (!controller.snapshot().intent) await controller.run(true);
      if (!view.isConnected || !controller.snapshot().canUpload) return;
      await consents.refresh();
      const storage = consents.snapshot().values.storage;
      if (!view.isConnected || storage?.state !== 'current') return;
      await controller.upload(storage);
      if (view.isConnected && controller.snapshot().status === 'verified') await processing.refresh();
    } finally {
      quickBusy = false;
      if (view.isConnected) drawUpload();
    }
  }
  if (quick) view.querySelector('[data-quick]').addEventListener('click', () => {
    if (view.isConnected) return quickUpload();
  });
  view.querySelector('[data-processing-read]').addEventListener('click', () => {
    if (view.isConnected && !CONFIG.USE_MOCK) return processing.refresh();
  });
  const events = ['auth:expired','auth:logout','hashchange','pagehide'];
  function dispose() {
    events.forEach(event => window.removeEventListener(event, dispose));
    input.value = ''; agree.checked = false; controller.close(); consents.close(); processing.close();
  }
  events.forEach(event => window.addEventListener(event, dispose));
  drawProcessing(processing.snapshot());
  draw(controller.snapshot());
  drawConsents(consents.snapshot());
}
