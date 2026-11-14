export function mountServerSync(app, doc) {
  const el = id => doc.getElementById(id);
  let pending = null, busy = false, generation = 0, input, operation = null;
  function controls() {
    el('agree').disabled = busy || !pending;
    el('confirm').disabled = busy || !pending || !el('agree').checked;
    el('cancel').disabled = busy || !pending;
    el('refresh').disabled = busy; el('retry').disabled = busy;
  }
  function reset() {
    ++generation; pending = null; operation = null; busy = false; el('agree').checked = false;
    el('refresh').hidden = el('retry').hidden = true;
    el('paper').textContent = ''; el('notice').textContent = '';
    el('status').textContent = '';
    controls();
  }
  function showStatus(output) {
    if (!output || output.operation_id !== operation || !['pending', 'syncing', 'synced',
      'retryable_failure', 'conflict', 'action_required'].includes(output.status)) throw new Error();
    el('status').textContent = `동기화 작업 ID: ${operation} · 상태: ${output.status}`;
    el('refresh').hidden = false;
    el('retry').hidden = !['pending', 'retryable_failure'].includes(output.status);
    if (output.status === 'conflict' || output.status === 'action_required')
      el('status').textContent += '\n계정·동의 또는 데이터 변경을 확인하세요. 이 작업을 자동 재전송하지 않습니다.';
  }
  app.ontoolinputpartial = app.ontoolcancelled = () => { input = null; reset(); };
  app.ontoolinput = event => { input = event.arguments; reset(); };
  app.ontoolresult = result => {
    const data = result?.structuredContent;
    const confirmationToken = data?.confirmation_token ?? result?._meta?.confirmation_token;
    if (input?.operation_id) {
      if (data?.operation_id !== input.operation_id) return;
      reset(); operation = input.operation_id;
      try { if (result?.isError) throw new Error(); showStatus(data); }
      catch { el('status').textContent = '기존 작업 상태를 확인하지 못했습니다.'; el('refresh').hidden = false; }
      controls(); return;
    }
    if (input === null || (input && input.paper_id !== data?.paper_id)) return;
    reset();
    if (!result?.isError && data?.status === 'confirmation_required'
        && typeof data.notice === 'string' && typeof data.paper_id === 'string'
        && typeof data.operation_id === 'string'
        && (data.title === null || typeof data.title === 'string')
        && typeof confirmationToken === 'string') {
      pending = {...data, confirmation_token: confirmationToken};
      el('paper').textContent = `논문: ${data.title || '(제목 없음)'} · 로컬 ID: ${data.paper_id}`;
      el('notice').textContent = data.notice;
      el('status').textContent = '아직 서버 전송 작업을 만들지 않았습니다.';
    } else el('status').textContent = '안내를 불러오지 못했습니다. 동기화 안내를 다시 요청하세요.';
    controls();
  };
  el('agree').onchange = controls;
  async function submit(accept) {
    if (!pending || busy || (accept && !el('agree').checked)) return;
    const data = pending, current = generation; busy = true; controls();
    el('status').textContent = accept ? '동기화 요청 중입니다.' : '취소 중입니다.';
    try {
      const result = await app.callServerTool({
        name: accept ? 'sync_paper_to_server' : 'cancel_paper_server_sync',
        arguments: accept ? {paper_id: data.paper_id, operation_id: data.operation_id,
          confirmation_token: data.confirmation_token}
          : {confirmation_token: data.confirmation_token},
      });
      if (current !== generation) return;
      const output = result?.structuredContent;
      if (result?.isError || !output || (accept ? output.operation_id !== data.operation_id
          : output.status !== 'cancelled')) throw new Error();
      if (accept) { operation = data.operation_id; showStatus(output); }
      else el('status').textContent = '취소했습니다. 서버 전송 작업을 만들지 않았습니다.';
    } catch {
      if (current !== generation) return;
      if (accept) { operation = data.operation_id; el('refresh').hidden = false; el('retry').hidden = true; }
      el('status').textContent = accept
        ? `동기화 결과를 확인하지 못했습니다. 작업 ID ${data.operation_id}로 상태를 조회하세요.`
        : '취소 결과를 확인하지 못했습니다.';
    }
    if (current === generation) { pending = null; busy = false; controls(); }
  }
  async function existing(retry) {
    if (busy || !operation || (retry && el('retry').hidden)) return;
    const current = generation, operationId = operation;
    busy = true; el('retry').hidden = true; controls();
    try {
      const result = await app.callServerTool({
        name: retry ? 'retry_paper_server_sync' : 'get_paper_server_sync',
        arguments: {operation_id: operationId},
      });
      if (current !== generation) return;
      if (result?.isError) throw new Error();
      showStatus(result?.structuredContent);
    } catch {
      if (current !== generation) return;
      el('status').textContent = `동기화 결과를 확인하지 못했습니다. 작업 ID ${operationId}로 상태를 다시 확인하세요.`;
      el('refresh').hidden = false;
    }
    if (current === generation) { busy = false; controls(); }
  }
  el('refresh').onclick = () => existing(false);
  el('retry').onclick = () => existing(true);
  el('confirm').onclick = () => submit(true);
  el('cancel').onclick = () => submit(false);
  controls();
  app.connect().catch(() => {
    ++generation; pending = null; busy = false; controls();
    el('status').textContent = '카드 연결에 실패했습니다. 동기화하지 않았습니다.';
  });
}
