const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
export function mountRemoteJobCancel(app, doc) {
  const el = id => doc.getElementById(id);
  let generation = 0, incoming = null, scope = null, busy = false;
  function clear(message, keep = false) {
    generation++; busy = false;
    if (!keep) scope = null;
    el('record').textContent = ''; el('status').textContent = message;
    el('confirm').disabled = true; el('refresh').disabled = !scope;
  }
  function result(reply, expected, lookup = false) {
    const data = reply?.structuredContent, value = data?.result;
    if (!reply?.isError && data?.status === 'ok' && value?.job_id === expected.job_id
        && value.status === 'cancelled' && Number.isSafeInteger(value.applied_revision) && value.applied_revision > 0
        && Number.isSafeInteger(value.revision) && value.revision >= value.applied_revision) {
      clear('취소가 저장됐습니다. 이미 발생한 외부 호출·비용은 되돌리지 않습니다.', true);
      el('record').textContent = `작업 ${value.job_id} · revision ${value.revision}`;
    } else if (lookup && !reply?.isError && data?.status === 'not_found') {
      clear('이 요청 키의 취소 기록이 없거나 접근할 수 없습니다. 취소 성공으로 처리하지 않으며 자동으로 다시 취소하지 않습니다.', true);
    } else {
      clear('결과를 확인하지 못했습니다. 같은 요청 키로 조회만 하세요. 자동으로 다시 취소하지 않습니다.', true);
    }
  }
  el('confirm').onclick = async () => {
    if (!scope || busy || el('confirm').disabled) return;
    const expected = {...scope}, current = generation;
    busy = true; el('confirm').disabled = true; el('refresh').disabled = true;
    try {
      const reply = await app.callServerTool({name:'confirm_remote_job_cancellation', arguments:{...expected, confirmed:true}});
      if (current === generation) result(reply, expected);
    } catch { if (current === generation) clear('응답이 유실됐습니다. 같은 요청 키로 결과를 조회하세요.', true); }
  };
  el('refresh').onclick = async () => {
    if (!scope || busy) return;
    const expected = {...scope}, current = generation;
    busy = true; el('confirm').disabled = true; el('refresh').disabled = true;
    try {
      const reply = await app.callServerTool({name:'get_remote_job_cancellation',arguments:{job_id:expected.job_id,request_key:expected.request_key}});
      if (current === generation) result(reply, expected, true);
    } catch { if (current === generation) clear('조회가 실패했습니다. 같은 키로 다시 조회하세요.', true); }
  };
  app.ontoolinput = event => { clear('취소 대상을 조회합니다.'); incoming = event?.arguments ?? null; };
  app.ontoolresult = reply => {
    if (!incoming) return;
    const expected = incoming; incoming = null;
    const value = reply?.structuredContent?.result;
    if (reply?.isError || reply?.structuredContent?.status !== 'ok' || !uuid(value?.job_id)
        || !uuid(value.request_key) || expected.job_id !== value.job_id || expected.request_key !== value.request_key) {
      clear('대상을 확인하지 못했습니다. 채팅에서 다시 조회하세요.'); return;
    }
    if (value.recovered) { scope = {job_id:value.job_id,request_key:value.request_key}; result({structuredContent:{status:'ok',result:value.recovered}},scope); return; }
    if (!Number.isSafeInteger(value.expected_revision) || value.expected_revision < 1
        || !/^[a-f0-9]{64}$/.test(value.binding ?? '') || value.job?.job_id !== value.job_id) {
      clear('확인 정보가 올바르지 않습니다.'); return;
    }
    scope = {job_id:value.job_id,request_key:value.request_key,expected_revision:value.expected_revision,binding:value.binding};
    el('record').textContent = `작업 ${value.job_id}\n${value.job.title ?? value.job.file_name ?? '제목 없음'}\n${value.job.status} · revision ${value.expected_revision}`;
    el('status').textContent = value.notice;
    el('confirm').disabled = value.can_cancel !== true || !['queued','processing'].includes(value.job.status);
    el('refresh').disabled = false;
  };
  app.ontoolcancelled = () => { incoming=null; clear('요청이 취소됐습니다.'); };
  doc.defaultView?.addEventListener('pagehide', () => { incoming=null; clear('카드를 다시 여세요.'); });
  app.connect().catch(() => clear('카드를 열 수 없습니다. 채팅에서 같은 요청 키로 조회하세요.'));
}
