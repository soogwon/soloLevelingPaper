const STORAGE = 'slp-translation-recovery-pending-v1';
const text = x => typeof x === 'string' && x.length > 0 && x.length <= 200;
const binding = x => x && text(x.version_id) && text(x.base_embedding_set_id) && text(x.request_key);
const same = (a, b) => binding(a) && binding(b) && ['version_id', 'base_embedding_set_id', 'request_key'].every(k => a[k] === b[k]);
const args = x => ({version_id: x.version_id, base_embedding_set_id: x.base_embedding_set_id, request_key: x.request_key});

function notAccepted(output, expected) {
  return output?.version_id === expected.version_id && output.base_embedding_set_id === expected.base_embedding_set_id
    && output.job_id === null && output.result_available === false
    && ['NO_TARGET', 'BASE_CHANGED', 'REQUEST_CONFLICT', 'IN_PROGRESS'].includes(output.status);
}

export function mountRecovery(app, doc, storage) {
  if (storage === undefined) { try { storage = doc.defaultView.localStorage; } catch { storage = null; } }
  const el = id => doc.getElementById(id);
  let pending = null, submitted = null, busy = false, generation = 0;
  let expectedInput;
  function controls() {
    el('agree').disabled = busy || !pending || !!submitted;
    el('confirm').disabled = busy || !pending || !!submitted || !el('agree').checked;
    el('cancel').disabled = busy || !pending || !!submitted;
    el('refresh').disabled = busy || !submitted;
  }
  function restore() {
    const raw = storage.getItem(STORAGE);
    if (!raw) return null;
    if (raw.length > 4096) throw new Error();
    const value = JSON.parse(raw);
    if (!binding(value)) throw new Error();
    return args(value);
  }
  function releaseStored(data) {
    // 다른 카드가 보관한 요청을 지우지 않는다. 로컬 기록 해제는 새 접수를 뜻하지 않는다.
    if (same(restore(), data)) storage.removeItem(STORAGE);
    if (same(submitted, data)) submitted = null;
  }
  function show(output, expected) {
    const allowed = ['ACCEPTED', 'IN_PROGRESS', 'COMPLETED', 'FAILED', 'INTERRUPTED', 'UNKNOWN', 'NO_TARGET', 'BASE_CHANGED', 'REQUEST_CONFLICT'];
    if (!output || !allowed.includes(output.status) || output.version_id !== expected.version_id
        || output.base_embedding_set_id !== expected.base_embedding_set_id) throw new Error();
    if (['NO_TARGET', 'BASE_CHANGED', 'REQUEST_CONFLICT'].includes(output.status)
        && (output.job_id !== null || output.result_available !== false)) throw new Error();
    const names = {ACCEPTED: '접수됨', IN_PROGRESS: '진행 중', FAILED: '실패', INTERRUPTED: '중단됨', UNKNOWN: '상태 확인 불가',
      NO_TARGET: '복구 대상 없음', BASE_CHANGED: '기준 색인 변경', REQUEST_CONFLICT: '요청 키 충돌'};
    if (output.status === 'COMPLETED') {
      if (!['FULL', 'PARTIAL'].includes(output.outcome) || !Number.isInteger(output.recovered_count)
          || output.recovered_count < 1 || !Number.isInteger(output.remaining_failed_count) || output.remaining_failed_count < 0
          || (output.outcome === 'FULL' && output.remaining_failed_count !== 0)
          || (output.outcome === 'PARTIAL' && output.remaining_failed_count < 1)) throw new Error();
      el('status').textContent = `${output.outcome === 'FULL' ? '전체' : '부분'} 복구 완료 · 복구 ${output.recovered_count}개 · 남은 실패 ${output.remaining_failed_count}개. 기존 학습 맥락은 유지됩니다.`;
    } else el('status').textContent = output.outcome === 'NO_PROGRESS'
      ? '복구 성공 0개입니다. 기존 색인을 유지합니다.'
      : `${names[output.status]}${text(output.job_id) ? ` · 작업 ID: ${output.job_id}` : ''}. 새 요청을 자동 접수하지 않습니다.`;
  }
  function reset(message) {
    ++generation; pending = null; busy = false; submitted = null;
    el('agree').checked = false; el('target').textContent = ''; el('notice').textContent = '';
    el('status').textContent = message;
    controls();
  }
  app.ontoolinputpartial = () => {
    expectedInput = null;
    reset('새 복구 안내를 기다리고 있습니다.');
  };
  app.ontoolinput = ({arguments: input}) => {
    expectedInput = text(input?.version_id) && text(input?.request_key) ? input : null;
    reset('복구 안내를 기다리고 있습니다.');
  };
  app.ontoolcancelled = () => {
    expectedInput = null;
    reset('복구 안내가 취소되었습니다.');
  };
  app.ontoolresult = result => {
    const data = result?.structuredContent;
    if (expectedInput === null || (expectedInput && data && (
      expectedInput.version_id !== data.version_id || expectedInput.request_key !== data.request_key
      || (expectedInput.base_embedding_set_id != null && expectedInput.base_embedding_set_id !== data.base_embedding_set_id)
    ))) return;
    reset('');
    try {
      submitted = restore();
      if (result?.isError || !binding(data) || typeof data.notice !== 'string') throw new Error();
      el('notice').textContent = data.notice;
      el('target').textContent = `버전: ${data.version_id} · 기준 색인: ${data.base_embedding_set_id} · 실패 구간: ${data.target_count ?? '확인 불가'}개`;
      if (submitted) {
        el('status').textContent = same(submitted, data)
          ? '보관한 같은 키로 상태를 먼저 조회하세요. 다시 접수하지 않습니다.'
          : '이전 복구 요청의 결과를 먼저 확인해야 합니다. 상태 조회는 보관한 이전 요청에 대해서만 수행합니다.';
      } else if (data.status === 'AVAILABLE' && text(result._meta?.confirmation_token)) {
        pending = {...args(data), confirmation_token: result._meta.confirmation_token};
        el('status').textContent = '아직 접수하지 않았습니다. 전송·비용 안내를 확인하고 동의 여부를 선택하세요.';
      } else el('status').textContent = `지금 복구를 접수할 수 없습니다. 상태: ${['NO_TARGET', 'BASE_CHANGED', 'UNAVAILABLE'].includes(data.status) ? data.status : '확인 불가'}`;
    } catch {
      pending = null;
      el('status').textContent = '안내 또는 보관한 요청을 확인하지 못했습니다. 복구를 접수하지 않습니다.';
    }
    controls();
  };
  el('agree').onchange = controls;
  el('confirm').onclick = async () => {
    if (busy || !pending || submitted || !el('agree').checked) return;
    const data = pending, current = generation;
    busy = true; controls();
    try {
      // 키 저장이 실패하면 서버를 호출하지 않는다. 토큰은 저장하지 않는다.
      const previous = restore();
      if (previous) {
        submitted = previous; pending = null;
        el('status').textContent = '보관된 이전 요청을 먼저 조회하세요. 새 복구를 접수하지 않았습니다.';
        return;
      }
      storage.setItem(STORAGE, JSON.stringify(args(data)));
      submitted = args(data); pending = null;
      if (!same(restore(), submitted)) throw new Error();
      const result = await app.callServerTool({name: 'recover_translation', arguments: {...args(data), confirmation_token: data.confirmation_token}});
      if (result?.isError) throw new Error();
      const output = result?.structuredContent;
      if (notAccepted(output, data)) {
        releaseStored(data);
        if (current !== generation) { controls(); return; }
        if (output.status === 'IN_PROGRESS') {
          el('status').textContent = '다른 작업이 진행 중이어서 이번 요청은 접수되지 않았습니다. 완료 후 새 복구 안내를 여세요.';
          return;
        }
      }
      if (current !== generation) return;
      show(output, data);
    } catch {
      if (current !== generation) return;
      pending = null;
      el('status').textContent = submitted
        ? '접수 결과를 확인하지 못했습니다. 같은 키로 상태를 조회하세요. 자동 재시도하지 않습니다.'
        : '요청 키를 보관하지 못해 접수하지 않았습니다.';
    } finally { if (current === generation) { busy = false; controls(); } }
  };
  el('cancel').onclick = async () => {
    if (busy || !pending || submitted) return;
    const data = pending, current = generation;
    pending = null; busy = true; controls();
    try {
      const result = await app.callServerTool({name: 'cancel_translation_recovery', arguments: {confirmation_token: data.confirmation_token}});
      if (result?.isError || result?.structuredContent?.status !== 'cancelled') throw new Error();
      if (current === generation) el('status').textContent = '취소했습니다. 복구를 접수하지 않았습니다.';
    } catch {
      if (current === generation) el('status').textContent = '취소 확인을 받지 못했습니다. 복구 요청은 보내지 않았습니다.';
    } finally { if (current === generation) { busy = false; controls(); } }
  };
  el('refresh').onclick = async () => {
    if (busy || !submitted) return;
    const data = submitted, current = generation;
    busy = true; controls();
    try {
      const result = await app.callServerTool({name: 'get_translation_recovery', arguments: args(data)});
      if (current !== generation) return;
      if (result?.isError) throw new Error();
      show(result?.structuredContent, data);
      if (['COMPLETED', 'FAILED', 'INTERRUPTED', 'NO_TARGET', 'BASE_CHANGED', 'REQUEST_CONFLICT'].includes(result.structuredContent.status)) {
        releaseStored(data);
      }
    } catch {
      if (current === generation) el('status').textContent = '상태를 확인하지 못했습니다. 같은 요청을 보관하며 새 작업을 접수하지 않습니다.';
    } finally { if (current === generation) { busy = false; controls(); } }
  };
  controls();
  app.connect().catch(() => {
    ++generation; pending = null; busy = false; controls();
    el('status').textContent = '카드 연결에 실패했습니다. 새 복구 요청을 보내지 않습니다.';
  });
}
