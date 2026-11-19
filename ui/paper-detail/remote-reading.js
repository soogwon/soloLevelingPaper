const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const STATUSES = {not_started: '시작 전', reading: '읽는 중', completed: '읽기 완료'};
const LOOKUP = 'show_remote_reading_progress';
const SAVE = 'save_remote_reading_progress_card';

function validPage(page) {
  return page === null || (Number.isSafeInteger(page) && page > 0);
}

function snapshot(reply, contextId, binding) {
  const data = reply?.structuredContent;
  const value = data?.result;
  const state = value?.state;
  if (reply?.isError || data?.status !== 'ok' || state?.context_id !== contextId
      || !/^[a-f0-9]{64}$/.test(value.binding ?? '') || (binding && value.binding !== binding)
      || typeof value.can_write !== 'boolean' || !Object.hasOwn(STATUSES, state.reading_status)
      || state.processing_status !== 'published' || !validPage(state.last_position?.pdf_page ?? null)
      || (state.reading_status === 'not_started' ? state.revision !== null
        : !Number.isSafeInteger(state.revision) || state.revision < 1)) return null;
  return value;
}

export function mountRemoteReading(app, doc, newKey = () => crypto.randomUUID()) {
  const el = id => doc.getElementById(id);
  let generation = 0, incoming = null, scope = null, state = null, pending = null, busy = false;

  function controls() {
    const editable = Boolean(scope?.canWrite && state && state.reading_status !== 'not_started' && !busy && !pending);
    el('complete').disabled = !editable || state?.reading_status !== 'reading';
    el('reopen').disabled = !editable || state?.reading_status !== 'completed';
    el('page').disabled = el('save').disabled = !editable;
    el('refresh').disabled = !scope || busy || Boolean(pending);
    el('retry').hidden = !pending;
    el('retry').disabled = !pending || busy;
  }

  function reset(message) {
    generation++;
    incoming = scope = state = pending = null;
    busy = false;
    el('title').textContent = el('record').textContent = el('page').value = '';
    el('status').textContent = message;
    controls();
  }

  function display(value, message) {
    state = value.state;
    scope = {contextId: state.context_id, binding: value.binding, canWrite: value.can_write};
    const page = state.last_position?.pdf_page ?? null;
    el('title').textContent = state.title ?? '';
    el('record').textContent =
      `처리: 게시 완료 · 읽기: ${STATUSES[state.reading_status]} · 최근 위치: ${page === null ? '없음' : `PDF ${page}페이지`}`;
    el('page').value = page ?? '';
    el('status').textContent = message || (state.reading_status === 'not_started'
      ? '학습을 먼저 시작하면 읽기 상태와 위치를 저장할 수 있습니다.'
      : value.can_write ? '' : '현재 연결에서는 조회만 할 수 있습니다.');
  }

  async function refresh(current, message = '') {
    const {contextId, binding} = scope;
    try {
      const reply = await app.callServerTool({
        name: LOOKUP, arguments: {context_id: contextId, expected_binding: binding},
      });
      if (current !== generation) return;
      const value = snapshot(reply, contextId, binding);
      if (value) display(value, message);
      else {
        const data = reply?.structuredContent;
        if (data?.result?.connection_changed || ['not_found', 'not_authorized'].includes(data?.status)) {
          reset('접근 권한이나 연결이 변경됐습니다. 채팅에서 카드를 다시 여세요.');
        } else {
          state = null;
          el('title').textContent = el('record').textContent = el('page').value = '';
          el('status').textContent = '최신 상태를 가져오지 못했습니다. 다시 조회해 주세요.';
        }
      }
    } catch {
      if (current !== generation) return;
      state = null;
      el('title').textContent = el('record').textContent = el('page').value = '';
      el('status').textContent = '최신 상태를 가져오지 못했습니다. 다시 조회해 주세요.';
    }
  }

  async function submit() {
    if (!pending || busy) return;
    const current = generation;
    busy = true;
    controls();
    el('status').textContent = '저장 중입니다.';
    try {
      const reply = await app.callServerTool({name: SAVE, arguments: pending});
      if (current !== generation) return;
      const data = reply?.structuredContent;
      if (!reply?.isError && (data?.result?.connection_changed || ['not_authorized', 'not_found'].includes(data?.status))) {
        reset('접근 권한이나 연결이 변경됐습니다. 채팅에서 카드를 다시 여세요.');
      } else if (!reply?.isError && data?.status === 'conflict') {
        pending = null;
        await refresh(current, '다른 곳에서 상태가 변경됐습니다. 최신 상태를 불러왔습니다.');
      } else if (!reply?.isError && data?.status === 'invalid_argument') {
        pending = null;
        el('status').textContent = '저장하지 못했습니다. PDF 페이지 번호를 확인해 주세요.';
      } else if (!reply?.isError && data?.status === 'ok' && data.result?.state?.context_id === scope.contextId
                 && Number.isSafeInteger(data.result.applied_revision) && data.result.applied_revision > 0) {
        pending = null;
        await refresh(current, '저장했습니다.');
      } else {
        el('status').textContent = '저장 결과를 확인하지 못했습니다. 같은 요청으로만 다시 저장할 수 있습니다.';
      }
    } catch {
      if (current === generation) el('status').textContent = '저장 응답이 유실됐습니다. 같은 요청으로만 다시 저장할 수 있습니다.';
    } finally {
      if (current === generation) {
        busy = false;
        controls();
      }
    }
  }

  function change(status, page) {
    if (!scope?.canWrite || !state || state.reading_status === 'not_started' || busy || pending || !validPage(page)) return;
    let key;
    try { key = newKey(); } catch { /* 키를 만들 수 없으면 저장을 중단한다. */ }
    if (typeof key !== 'string' || !UUID.test(key)) {
      el('status').textContent = '요청 키를 만들 수 없어 저장하지 않았습니다.';
      return;
    }
    pending = Object.freeze({context_id: scope.contextId, request_key: key, status, pdf_page: page,
      expected_revision: state.revision, expected_binding: scope.binding});
    return submit();
  }

  el('complete').onclick = () => {
    if (!el('complete').disabled) return change('completed', state.last_position?.pdf_page ?? null);
  };
  el('reopen').onclick = () => {
    if (!el('reopen').disabled) return change('reading', state.last_position?.pdf_page ?? null);
  };
  el('position').onsubmit = event => {
    event.preventDefault();
    if (el('save').disabled || !el('page').validity.valid) return;
    return change(state.reading_status, el('page').value === '' ? null : el('page').valueAsNumber);
  };
  el('retry').onclick = submit;
  el('refresh').onclick = async () => {
    if (!scope || busy || pending) return;
    const current = generation;
    busy = true;
    controls();
    await refresh(current);
    if (current === generation) {
      busy = false;
      controls();
    }
  };
  app.ontoolinputpartial = () => reset('읽기 상태를 불러오는 중입니다.');
  app.ontoolinput = event => {
    reset('읽기 상태를 불러오는 중입니다.');
    const id = event?.arguments?.context_id;
    if (typeof id === 'string' && UUID.test(id)) incoming = id.toLowerCase();
  };
  app.ontoolresult = reply => {
    if (!incoming) return;
    const value = snapshot(reply, incoming);
    incoming = null;
    if (!value) return reset('읽기 상태를 확인할 수 없습니다. 채팅에서 다시 열어 주세요.');
    display(value);
    controls();
  };
  app.ontoolcancelled = () => reset('요청이 취소됐습니다.');
  doc.defaultView?.addEventListener('pagehide', () => reset('카드를 다시 열어 주세요.'));
  controls();
  app.connect().catch(() => reset('카드에 연결할 수 없습니다. 채팅에서 읽기 상태를 조회해 주세요.'));
}
