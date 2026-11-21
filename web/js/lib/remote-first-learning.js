import { api } from '../api/index.js';
import { html, setHTML } from './html.js';
import { copyText } from './ui.js';
import { consentMessages, createUploadConsents } from './upload-consents.js';
import { accountConsentCurrent } from './account-consents.js';

const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
export function hasFirstLearningSource(paper) {
  return uuid(paper.paper_id) && uuid(paper.selected_version_id)
    && paper.file_access?.reason === 'file_ready';
}

const jobLabels = {
  queued: '처리 대기 중', running: '처리 중', succeeded: '처리는 완료됐고 게시 상태를 확인 중이에요.',
  failed: '처리에 실패했어요.', cancelled: '처리가 취소됐어요.',
};

export async function remoteFirstLearning(view, paper, version, client = api) {
  if (!view || !uuid(paper) || !uuid(version)) return;
  let closed = false, busy = false, source = null, job = null, checkedJob = false;
  let approved = false, pendingRequest = null, requestIntent = null, message = '';
  let poll = null;
  const events = ['auth:logout', 'auth:expired', 'hashchange', 'pagehide'];
  const current = () => !closed && view.isConnected;
  const consents = createUploadConsents({api:client, changed: draw});
  const close = () => {
    closed = true;
    if (poll !== null) window.clearTimeout(poll);
    consents.close();
    view.textContent = '';
    events.forEach(event => window.removeEventListener(event, close));
  };
  events.forEach(event => window.addEventListener(event, close));

  function isEligible() {
    const consent = consents.snapshot();
    return checkedJob && !job && consent.status === 'ready'
      && consent.values.storage?.state === 'current' && consent.values.ai_provider?.state === 'current';
  }

  function draw() {
    if (!current()) return;
    const consent = consents.snapshot();
    const eligible = isEligible();
    // 가입 때 두 동의를 받은 계정은 목적별 동의 안내 없이 접수 상태만 보여준다.
    const byAccount = eligible && accountConsentCurrent('storage') && accountConsentCurrent('ai_provider');
    const prompt = `연결된 Solo Leveling 원격 MCP에서 논문 ID ${paper}, PDF 판본 ID ${version}을 열고 이 판본으로 원격 학습을 시작해 줘. open_remote_paper로 연 학습 맥락에 start_remote_learning을 호출해 줘.`;
    setHTML(view, html`
      <h2 class="panel-title">학습 처리</h2>
      <p role="status">${message || (job ? jobLabels[job.status] : '')}</p>
      ${source?.active_publication_id ? html`
        <p>이 판본의 학습 자료가 게시됐어요. Claude에서 연결된 Solo Leveling MCP에 아래 요청을 보내세요.</p>
        <label>Claude에 보낼 요청<textarea readonly rows="4">${prompt}</textarea></label>
        <button type="button" class="btn btn-primary" data-copy-learning>Claude 요청 복사</button>
      ` : source ? html`
        ${!job ? html`
          <p>등록된 PDF를 사용합니다. 별도 접수 후 worker가 PDF를 처리하고 학습 자료를 게시해요.</p>
          ${byAccount ? html`<p>가입 때 동의한 내용으로 학습 자료 만들기를 접수해요.</p>`
          : consent.notice ? html`<p>고지 버전: ${consent.notice.version}</p><p>${consent.notice.ai_provider}</p>
            <p>${consentMessages[consent.busy ? 'busy' : consent.status]}</p>
            ${consent.values.storage?.state !== 'current' ? html`<p>현재 PDF 보관 동의가 필요해요.</p><p>${consent.notice.storage}</p>
              <label><input type="checkbox" data-storage-check ${busy || !consent.canGrant.storage ? 'disabled' : ''}> PDF 보관에 동의해요.</label>
              <button type="button" class="btn" data-storage-grant disabled>보관 동의 저장</button>` : ''}
            ${consent.canGrant.ai_provider ? html`<label><input type="checkbox" data-ai-check ${approved ? 'checked' : ''}> 위 고지를 읽고 AI 전송에 동의해요.</label>
              <button type="button" class="btn" data-ai-grant ${!approved || busy ? 'disabled' : ''}>AI 전송 동의 저장</button>` : ''}
            ${consent.values.ai_provider?.state === 'current' ? html`<p>AI 전송에 동의했어요.</p>` : ''}
          ` : ''}
          <button type="button" class="btn btn-primary" data-admit ${!eligible || busy || consent.busy ? 'disabled' : ''}>${pendingRequest ? '같은 접수 요청 확인' : '학습 처리 접수'}</button>
        ` : html`<p>작업 ID: ${job.job_id}</p>`}
      ` : ''}
      <button type="button" class="btn" data-check ${busy || consent.busy ? 'disabled' : ''}>처리·게시 상태 다시 조회</button>`);
    const copy = view.querySelector('[data-copy-learning]');
    if (copy) copy.onclick = async () => {
      const copied = await copyText(prompt);
      if (!current() || !source?.active_publication_id) return;
      message = copied ? '요청을 복사했어요. Claude에 붙여넣으세요.' : '위 요청을 직접 복사해 주세요.';
      draw();
    };
    const check = view.querySelector('[data-ai-check]');
    const grant = view.querySelector('[data-ai-grant]');
    if (check && grant) {
      check.onchange = () => { approved = check.checked; grant.disabled = !approved; };
      grant.onclick = () => { const consentApproved = approved; approved = false; return consents.grant('ai_provider', consentApproved); };
    }
    const storageCheck = view.querySelector('[data-storage-check]');
    const storageGrant = view.querySelector('[data-storage-grant]');
    if (storageCheck && storageGrant) {
      storageCheck.onchange = () => { storageGrant.disabled = !storageCheck.checked; };
      storageGrant.onclick = () => consents.grant('storage', storageCheck.checked);
    }
    const admit = view.querySelector('[data-admit]');
    if (admit) admit.onclick = () => submit(eligible);
    view.querySelector('[data-check]').onclick = refresh;
  }

  function validateJob(value) {
    if (!value || value.paper_id !== paper || value.version_id !== version
        || !uuid(value.job_id) || !Object.hasOwn(jobLabels, value.status)) throw Error('invalid_job');
    return value;
  }

  function scheduleRefresh() {
    if (poll !== null) window.clearTimeout(poll);
    poll = null;
    if (current() && !source?.active_publication_id
        && ['queued', 'running', 'succeeded'].includes(job?.status)) {
      poll = window.setTimeout(() => { poll = null; return refresh(); }, 5000);
    }
  }

  async function refresh() {
    if (!current() || busy || consents.snapshot().busy) return;
    source = null;
    busy = true; approved = false; checkedJob = false; message = '등록된 PDF와 처리 상태를 확인 중이에요.'; draw();
    try {
      const next = await client.getRemoteProcessingSource(paper, version);
      if (!current()) return;
      if (!next || !uuid(next.intent_id) || next.paper_id !== paper || next.version_id !== version
          || (next.active_publication_id !== null && !uuid(next.active_publication_id))) throw Error('invalid_source');
      if (requestIntent !== next.intent_id) { pendingRequest = null; job = null; }
      requestIntent = next.intent_id;
      source = next;
      if (!next.active_publication_id) {
        try {
          const value = await client.getRemoteProcessing(next.intent_id);
          if (!current()) return;
          job = validateJob(value);
        } catch (error) {
          if (!current()) return;
          if (error?.status !== 404 || error.code !== 'remote_processing_not_found') throw error;
          job = null;
        }
        checkedJob = true;
        if (!job) {
          consents.bind({intent_id:next.intent_id,status:'verified'});
          await consents.refresh();
          if (!current()) return;
          const refreshed = consents.snapshot();
          if (refreshed.status === 'ready' && pendingRequest
              && (refreshed.values.storage?.consent_id !== pendingRequest.body.storage_consent_id
                  || refreshed.values.ai_provider?.consent_id !== pendingRequest.body.ai_consent_id)) {
            pendingRequest = null;
          }
        }
      }
      if (current()) message = '';
    } catch (error) {
      if (current()) message = error?.status === 404 && error.code === 'remote_upload_not_found'
        ? '이 판본에 처리할 수 있는 서버 PDF가 없어요.'
        : '처리 준비 상태를 확인하지 못했어요. 다시 조회해 주세요.';
    } finally {
      if (current()) { busy = false; draw(); scheduleRefresh(); }
    }
  }

  async function submit(eligible) {
    if (!eligible || !current() || busy || !checkedJob || job) return;
    const { values, busy: consentBusy, status } = consents.snapshot();
    if (consentBusy || status !== 'ready' || values.storage?.state !== 'current'
        || values.ai_provider?.state !== 'current') return;
    const body = {
      storage_consent_id: values.storage.consent_id,
      ai_consent_id: values.ai_provider.consent_id,
    };
    if (pendingRequest && (pendingRequest.body.storage_consent_id !== body.storage_consent_id
        || pendingRequest.body.ai_consent_id !== body.ai_consent_id)) {
      checkedJob = false;
      message = '동의가 바뀌었어요. 처리 상태를 다시 조회한 뒤 새로 접수해 주세요.';
      draw();
      return;
    }
    pendingRequest ??= {body, key:crypto.randomUUID()};
    busy = true; message = '학습 처리 요청을 접수 중이에요.'; draw();
    try {
      const value = await client.admitRemoteProcessing(
        source.intent_id, pendingRequest.body, pendingRequest.key,
      );
      if (current()) { job = validateJob(value); message = ''; }
    } catch (error) {
      if (current()) {
        if (error?.status === 409) {
          checkedJob = false;
          message = '지금은 학습 처리를 접수할 수 없어요. 동의와 처리 상태를 다시 확인해 주세요.';
        } else {
          message = '접수 결과를 확인하지 못했어요. 같은 요청을 확인하거나 처리 상태를 다시 조회해 주세요.';
        }
      }
    } finally {
      if (current()) { busy = false; draw(); scheduleRefresh(); }
    }
  }

  await refresh();
  // 가입 때 두 동의를 받았으면 접수 버튼을 누르지 않아도 바로 접수한다. 실패하면 버튼이 남는다.
  if (current() && !pendingRequest && isEligible()
      && accountConsentCurrent('storage') && accountConsentCurrent('ai_provider')) await submit(true);
}
