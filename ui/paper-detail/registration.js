function payload(result) {
  if (result?.structuredContent) return result.structuredContent;
  for (const part of result?.content || []) {
    if (part.type !== 'text') continue;
    try {
      const data = JSON.parse(part.text);
      if (data && typeof data === 'object') return data;
    } catch { /* 일반 안내문은 등록 데이터로 사용하지 않는다. */ }
  }
}

function validInput(data) {
  return data?.source?.kind === 'local_file' && typeof data.source.relative_path === 'string'
    && data.source.relative_path.length > 0 && typeof data.request_key === 'string' && data.request_key.length > 0;
}

export function mountRegistration(app, doc) {
  const el = id => doc.getElementById(id);
  let pending = null, busy = false, generation = 0;
  function controls() {
    el('agree').disabled = busy || !pending;
    el('confirm').disabled = busy || !pending || !el('agree').checked;
    el('cancel').disabled = busy || !pending;
  }
  let input = null;
  function resetInput(args) {
    input = validInput(args) ? args : null;
    ++generation;
    pending = null;
    busy = false;
    el('agree').checked = false;
    el('filename').textContent = '';
    el('notice').textContent = '';
    controls();
  }
  app.ontoolinput = event => resetInput(event?.arguments);
  app.ontoolinputpartial = () => resetInput(null);
  app.ontoolcancelled = () => {
    resetInput(null);
    el('status').textContent = '등록 안내가 취소되었습니다.';
  };
  app.ontoolresult = async result => {
    const current = ++generation;
    pending = null;
    busy = false;
    el('agree').checked = false;
    el('filename').textContent = '';
    el('notice').textContent = '';
    controls();
    let data = payload(result);
    const args = input || (validInput(data) ? data : null);
    input = null;
    try {
      if (result?.isError || !args) throw new Error();
      if (!result?._meta?.confirmation_token || !validInput(data)) {
        // 호스트 알림에서 생략된 카드 전용 메타데이터를 직접 응답으로 받는다.
        el('status').textContent = '등록 안내를 확인하고 있습니다.';
        result = await app.callServerTool({name: 'prepare_paper_registration',
          arguments: {source: args.source, request_key: args.request_key}});
        if (current !== generation) return;
        data = payload(result);
      }
      const token = result?._meta?.confirmation_token;
      if (result?.isError || !validInput(data) || data.status !== 'confirmation_required'
          || typeof data.notice !== 'string' || !data.notice || typeof token !== 'string' || !token
          || data.source.relative_path !== args.source.relative_path || data.request_key !== args.request_key) throw new Error();
      pending = {...data, confirmation_token: token};
      el('filename').textContent = data.source.relative_path;
      el('notice').textContent = data.notice;
      el('status').textContent = '아직 등록하지 않았습니다. 내용을 확인한 뒤 동의 여부를 선택하세요.';
    } catch {
      if (current !== generation) return;
      el('status').textContent = '안내를 불러오지 못했습니다. 등록 안내를 다시 요청하세요.';
    }
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
      const output = payload(result);
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
