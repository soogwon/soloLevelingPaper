const uuid = v => typeof v === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(v);
export function mountRemoteAnnotation(app, doc) {
  const el = id => doc.getElementById(id);
  let generation = 0, scope = null, record = null, busy = false, incoming = null;
  function clear(message, keep = false) {
    ++generation; record = null; busy = false;
    if (!keep) scope = null;
    el('record').textContent = ''; el('status').textContent = message;
    el('confirm').disabled = true; el('refresh').disabled = !scope;
  }
  function render(reply, expected = null) {
    clear('결과를 확인할 수 없습니다. 새 키로 재생성하지 말고 채팅에서 다시 조회하세요.', !!expected);
    const data = reply?.structuredContent, result = data?.result, recovery = result?.recovery, a = recovery?.annotation;
    if (reply?.isError || data?.status !== 'ok' || !uuid(result?.context_id)
        || typeof result.request_key !== 'string' || !/^[a-f0-9]{64}$/.test(result.binding ?? '')
        || (expected && (result.context_id !== expected.context_id || result.request_key !== expected.request_key || result.binding !== expected.binding))
        || recovery?.request_key !== result.request_key || recovery?.recovery !== 'exact'
        || !['committed','deleted'].includes(recovery.state) || !uuid(a?.annotation_id)
        || a.context_id !== result.context_id || !['note','highlight'].includes(a.kind)
        || !Number.isSafeInteger(a.revision) || a.revision < 1 || typeof a.body !== 'string' || !Array.isArray(a.segments)) return;
    scope = {context_id:result.context_id, request_key:result.request_key, binding:result.binding};
    el('refresh').disabled = false;
    if (recovery.state === 'deleted') {
      el('status').textContent = '삭제된 기록입니다. 본문을 표시하거나 재생성하지 않습니다.'; return;
    }
    if (!a.segments.every(s => s && ['original','translation'].includes(s.target) && typeof s.quote_snapshot === 'string')) return;
    record = a;
    el('record').textContent = `${a.kind} · ${a.annotation_id} · revision ${a.revision}\n${a.body}\n`
      + a.segments.map(s => `${s.target === 'translation' ? '번역' : '원문'}: ${s.quote_snapshot}`).join('\n');
    el('status').textContent = '현재 저장 결과입니다. 내용을 확인한 뒤 수신을 확정하세요.';
    el('confirm').disabled = false;
  }
  el('refresh').onclick = async () => {
    if (!scope || busy) return;
    const expected = {...scope}; clear('같은 요청을 조회하는 중입니다.', true);
    const current = generation; busy = true; el('refresh').disabled = true;
    try {
      const reply = await app.callServerTool({name:'show_remote_annotation_recovery', arguments:{context_id:expected.context_id, request_key:expected.request_key, expected_binding:expected.binding}});
      if (current === generation) render(reply, expected);
    } catch { if (current === generation) clear('조회 결과가 불명확합니다. 같은 요청을 다시 조회하세요.', true); }
  };
  el('confirm').onclick = async () => {
    if (!scope || !record || busy) return;
    const expected = {...scope}, saved = record, current = generation;
    busy = true; el('confirm').disabled = true; el('refresh').disabled = true;
    try {
      const reply = await app.callServerTool({name:'confirm_remote_annotation_card', arguments:{context_id:expected.context_id, request_key:expected.request_key, expected_binding:expected.binding, annotation_id:saved.annotation_id, revision:saved.revision}});
      if (current !== generation) return;
      clear(!reply?.isError && reply?.structuredContent?.status === 'ok' && reply.structuredContent.result?.confirmed === true
        ? '수신을 확정했습니다. 새 기록은 생성하지 않았습니다.' : '수신 확정을 확인하지 못했습니다. 현재 결과를 다시 조회하세요.', true);
    } catch { if (current === generation) clear('수신 확정 결과가 불명확합니다. 같은 요청을 다시 조회하세요.', true); }
  };
  app.ontoolinput = event => {
    clear('기존 요청을 조회하는 중입니다.');
    incoming = event?.arguments ?? null;
  };
  app.ontoolresult = reply => {
    if (!incoming) return;
    const result = reply?.structuredContent?.result;
    if (result?.context_id !== incoming.context_id || result?.request_key !== incoming.request_key) {
      incoming = null; clear('요청과 결과가 일치하지 않습니다. 채팅에서 다시 조회하세요.'); return;
    }
    incoming = null; render(reply);
  };
  app.ontoolcancelled = () => { incoming = null; clear('조회가 취소됐습니다.'); };
  doc.defaultView?.addEventListener('pagehide', () => { incoming = null; clear('카드를 다시 열어 주세요.'); });
  app.connect().catch(() => clear('카드를 사용할 수 없습니다. 채팅에서 기존 요청 키로 조회하세요.'));
}
