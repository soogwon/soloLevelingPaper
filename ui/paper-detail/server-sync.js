export function mountServerSync(app, doc) {
  const el = id => doc.getElementById(id);
  let pending = null, busy = false, generation = 0, input;
  function controls() {
    el('agree').disabled = busy || !pending;
    el('confirm').disabled = busy || !pending || !el('agree').checked;
    el('cancel').disabled = busy || !pending;
  }
  function reset() {
    ++generation; pending = null; busy = false; el('agree').checked = false;
    el('paper').textContent = ''; el('notice').textContent = '';
    el('status').textContent = '';
    controls();
  }
  app.ontoolinputpartial = app.ontoolcancelled = () => { input = null; reset(); };
  app.ontoolinput = event => { input = event.arguments; reset(); };
  app.ontoolresult = result => {
    const data = result?.structuredContent;
    const confirmationToken = data?.confirmation_token ?? result?._meta?.confirmation_token;
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
      el('status').textContent = accept
        ? `동기화 작업 ID: ${output.operation_id} · 상태: ${output.status}`
        : '취소했습니다. 서버 전송 작업을 만들지 않았습니다.';
    } catch {
      if (current !== generation) return;
      el('status').textContent = accept
        ? `동기화 결과를 확인하지 못했습니다. 작업 ID ${data.operation_id}로 상태를 조회하세요.`
        : '취소 결과를 확인하지 못했습니다.';
    }
    if (current === generation) { pending = null; busy = false; controls(); }
  }
  el('confirm').onclick = () => submit(true);
  el('cancel').onclick = () => submit(false);
  controls();
  app.connect().catch(() => {
    ++generation; pending = null; busy = false; controls();
    el('status').textContent = '카드 연결에 실패했습니다. 동기화하지 않았습니다.';
  });
}
