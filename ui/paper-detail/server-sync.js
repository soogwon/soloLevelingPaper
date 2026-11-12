export function mountServerSync(app, doc) {
  const el = id => doc.getElementById(id);
  let pending = null, busy = false, generation = 0;
  function controls() {
    el('agree').disabled = busy || !pending;
    el('confirm').disabled = busy || !pending || !el('agree').checked;
    el('cancel').disabled = busy || !pending;
  }
  app.ontoolresult = result => {
    ++generation; pending = null; busy = false; el('agree').checked = false;
    el('paper').textContent = ''; el('notice').textContent = '';
    const data = result?.structuredContent;
    if (!result?.isError && data?.status === 'confirmation_required'
        && typeof data.notice === 'string' && typeof data.paper_id === 'string'
        && typeof data.operation_id === 'string'
        && (data.title === null || typeof data.title === 'string')
        && typeof result._meta?.confirmation_token === 'string') {
      pending = {...data, confirmation_token: result._meta.confirmation_token};
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
      if (result?.isError || !output || (accept ? typeof output.operation_id !== 'string'
          : output.status !== 'cancelled')) throw new Error();
      el('status').textContent = accept
        ? `동기화 작업 ID: ${output.operation_id} · 상태: ${output.status}`
        : '취소했습니다. 서버 전송 작업을 만들지 않았습니다.';
    } catch {
      if (current !== generation) return;
      el('status').textContent = accept
        ? '동기화 결과를 확인하지 못했습니다. 자동 재시도하지 않습니다.'
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
