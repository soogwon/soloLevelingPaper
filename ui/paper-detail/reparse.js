export function mountReparse(app, doc, storage) {
  const el = id => doc.getElementById(id);
  if (storage === undefined) {
    try { storage = doc.defaultView.localStorage; } catch { storage = null; }
  }
  let input, generation = 0, busy = false, token, target, job;
  const slot = () => 'slp-reparse-v1:' + target.context_id;
  const id = value => typeof value === 'string' && value.trim() && value.length <= 200;
  function controls() {
    el('agree').disabled = !token || busy;
    el('confirm').disabled = !token || busy || !el('agree').checked;
    el('cancel').disabled = !token || busy;
    el('refresh').disabled = !target || busy;
    el('select').disabled = !job || busy;
  }
  function reset() {
    ++generation;
    busy = false; target = token = job = null;
    el('agree').checked = false;
    for (const key of ['target', 'notice', 'status']) el(key).textContent = '';
    controls();
  }
  function result(data) {
    if (!data || data.context_id !== target.context_id || data.request_key !== target.request_key) throw Error();
    job = null;
    const names = {ACCEPTED:'접수됨', IN_PROGRESS:'진행 중', COMPLETED:'게시 완료', NOT_FOUND:'미접수',
      FAILED:'실패', INTERRUPTED:'중단됨', BASE_CHANGED:'기준 색인 변경', REQUEST_CONFLICT:'요청 키 충돌',
      BLOCKED:'외부 번역 차단', AVAILABLE:'접수 전'};
    if (!Object.hasOwn(names, data.status)) throw Error();
    if (data.status === 'COMPLETED' && data.result_available && id(data.new_embedding_set_id)) job = data.job_id;
    const unaccepted = data.status === 'IN_PROGRESS' && !data.job_id;
    el('status').textContent = (unaccepted ? '다른 작업 진행 중 · 이번 요청은 미접수' : names[data.status])
      + (data.job_id ? ' · 작업 ID: ' + data.job_id : '') + (data.reason_code ? ' · ' + data.reason_code : '');
    if ((unaccepted || ['COMPLETED','FAILED','INTERRUPTED','NOT_FOUND','REQUEST_CONFLICT','BASE_CHANGED'].includes(data.status))
        && storage?.getItem(slot()) === target.request_key) storage.removeItem(slot());
  }
  app.ontoolinputpartial = app.ontoolcancelled = () => { input = null; reset(); };
  app.ontoolinput = event => { input = event.arguments; reset(); };
  app.ontoolresult = response => {
    const data = response?.structuredContent;
    if (input === null || (input && (data?.context_id !== input.context_id || data?.request_key !== input.request_key))) return;
    reset();
    if (response?.isError || !id(data?.context_id) || !id(data?.request_key)) return;
    target = {context_id:data.context_id, request_key:data.request_key};
    el('target').textContent = data.filename;
    el('notice').textContent = data.notice + ' 번역 제공자: ' + data.translation.provider + ' · 모델: ' + data.translation.model;
    try {
      const previous = storage?.getItem(slot());
      if (previous && previous !== target.request_key) {
        target.request_key = previous;
        el('status').textContent = '이 맥락에 보관된 이전 요청의 상태를 먼저 조회하세요.';
      } else {
        result(data);
        if (!previous && data.status === 'AVAILABLE') token = response._meta?.confirmation_token;
      }
    } catch { el('status').textContent = '요청 기록을 확인할 수 없습니다.'; }
    controls();
  };
  async function action(kind) {
    const control = el(kind);
    if (control.disabled || !target) return;
    const current = generation;
    let name, args = {...target};
    busy = true; controls();
    try {
      if (kind === 'confirm') {
        if (storage.getItem(slot())) throw Error();
        storage.setItem(slot(), target.request_key);
        if (storage.getItem(slot()) !== target.request_key) throw Error();
        name = 'submit_paper_reparse'; args.confirmation_token = token;
      } else if (kind === 'cancel') {
        name = 'cancel_paper_reparse'; args = {confirmation_token:token};
      } else if (kind === 'select') {
        name = 'select_reparsed_context'; args.job_id = job;
      } else name = 'get_paper_reparse';
      token = null;
      const response = await app.callServerTool({name, arguments:args});
      if (current !== generation) return;
      if (response.isError) throw Error();
      const data = response.structuredContent;
      if (kind === 'cancel') el('status').textContent = '접수 전 동의를 취소했습니다.';
      else if (kind === 'select' && data.job_id === job && id(data.context_id)) {
        el('status').textContent = '새 학습 맥락 ID: ' + data.context_id;
      } else result(data);
    } catch {
      if (current === generation) {
        token = null;
        el('status').textContent = '결과를 확인하지 못했습니다. 같은 키로 상태를 조회하세요.';
      }
    } finally { if (current === generation) { busy = false; controls(); } }
  }
  for (const kind of ['confirm','cancel','refresh','select']) el(kind).onclick = () => action(kind);
  el('agree').onchange = controls;
  reset();
  app.connect().catch(() => { input = null; reset(); el('status').textContent = '카드에 연결하지 못했습니다.'; });
}
