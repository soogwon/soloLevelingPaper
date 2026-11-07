export function mountRegistration(app, doc) {
  const el = id => doc.getElementById(id);
  let pending = null, busy = false, generation = 0;
  function controls() {
    el('agree').disabled = busy || !pending;
    el('confirm').disabled = busy || !pending || !el('agree').checked;
    el('cancel').disabled = busy || !pending;
  }
  app.ontoolresult = result => {
    ++generation;
    pending = null;
    busy = false;
    el('agree').checked = false;
    el('filename').textContent = '';
    el('notice').textContent = '';
    const data = result?.structuredContent;
    if (!result?.isError && data?.status === 'confirmation_required'
        && typeof data.notice === 'string' && typeof result._meta?.confirmation_token === 'string'
        && data.source?.kind === 'local_file' && typeof data.source.relative_path === 'string'
        && typeof data.request_key === 'string') {
      pending = {...data, confirmation_token: result._meta.confirmation_token};
      el('filename').textContent = data.source.relative_path;
      el('notice').textContent = data.notice;
      el('status').textContent = '아직 등록하지 않았습니다. 내용을 확인한 뒤 동의 여부를 선택하세요.';
    } else el('status').textContent = '안내를 불러오지 못했습니다. 등록 안내를 다시 요청하세요.';
    controls();
  };
  el('agree').onchange = controls;
  async function submit(accept) {
    if (!pending || busy || (accept && !el('agree').checked)) return;
    const data = pending, current = generation;
    busy = true;
    controls();
    el('status').textContent = accept ? '등록 요청 중입니다.' : '취소 중입니다.';
    try {
      const result = await app.callServerTool({
        name: accept ? 'add_paper' : 'cancel_paper_registration',
        arguments: accept ? {source: data.source, request_key: data.request_key,
          confirmation_token: data.confirmation_token} : {confirmation_token: data.confirmation_token},
      });
      if (current !== generation) return;
      const output = result?.structuredContent;
      if (result?.isError || !output || (accept ? !output.job_id : output.status !== 'cancelled')) throw new Error();
      el('status').textContent = accept
        ? `등록 작업을 시작했습니다. 작업 ID: ${output.job_id} · 상태: ${output.status}`
        : '취소했습니다. 등록 작업을 시작하지 않았습니다.';
    } catch {
      if (current !== generation) return;
      el('status').textContent = accept
        ? '등록 결과를 확인하지 못했습니다. 자동 재시도하지 않습니다. 작업 목록을 먼저 조회하세요.'
        : '취소 확인을 받지 못했습니다. 등록 버튼을 누르지 않았다면 작업은 시작되지 않습니다.';
    }
    if (current === generation) { pending = null; busy = false; controls(); }
  }
  el('confirm').onclick = () => submit(true);
  el('cancel').onclick = () => submit(false);
  controls();
  app.connect().catch(() => {
    ++generation; pending = null; busy = false; controls();
    el('status').textContent = '카드 연결에 실패했습니다. 등록하지 않았습니다.';
  });
}
