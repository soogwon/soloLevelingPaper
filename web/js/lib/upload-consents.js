// 동의는 현재 선택·로그인 세대에만 연결한다. PDF 전송·처리 접수는 하지 않는다.
export const consentPurposes = ['storage', 'ai_provider'];
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const text = value => typeof value === 'string' && !!value.trim();
export const consentMessages = {
  empty: '등록 준비를 저장한 뒤 동의 안내를 조회해 주세요.',
  unread: '현재 고지와 동의 상태를 먼저 조회해 주세요.',
  busy: '동의 상태를 확인 중이에요.',
  ready: '아래 고지와 동의 상태를 확인해 주세요. 동의 저장만으로 파일 전송이나 AI 처리를 시작하지 않아요.',
  uncertain: '결과를 확인하지 못했어요. 상태를 다시 조회하거나 같은 승인 요청을 확인하세요. 자동으로 새 동의를 만들지 않아요.',
  refresh: '고지 또는 동의 상태가 바뀌었어요. 안내를 다시 조회한 뒤 확인해 주세요.',
  blocked: '동의를 사용할 수 없어요. 로그인 상태와 등록 준비 상태를 확인해 주세요.',
};

function checkedConsent(value, purpose, notice) {
  if (!value || !uuid.test(value.consent_id) || value.purpose !== purpose
      || !['current','stale','revoked'].includes(value.state)
      || !Number.isSafeInteger(value.revision) || value.revision < 1
      || !text(value.notice_version) || !Number.isFinite(Date.parse(value.granted_at))
      || (value.state === 'current' && value.notice_version !== notice)
      || (value.state === 'stale' && value.notice_version === notice)
      || (value.state === 'revoked' ? !Number.isFinite(Date.parse(value.revoked_at)) : value.revoked_at !== null)) {
    throw new Error('invalid_consent_response');
  }
  return {...value};
}

export function createUploadConsents({api, changed = () => {}, newKey = () => crypto.randomUUID()}) {
  let generation = 0, closed = false, intent = null, busy = false, status = 'empty', notice = null;
  let values = {storage:null, ai_provider:null}, keys = {};
  const usable = () => !closed && !!intent && ['created','uploading','verified'].includes(intent.status);
  const snapshot = () => ({status, busy, notice:notice ? {...notice} : null,
    values:structuredClone(values), canRead:usable() && !busy,
    canGrant:Object.fromEntries(consentPurposes.map(p => [p, usable() && !busy && !!notice
      && ['ready','uncertain'].includes(status) && values[p]?.state !== 'current'
      && (p === 'storage' || intent.status === 'verified')])),
    canRevoke:Object.fromEntries(consentPurposes.map(p => [p, usable() && !busy && !!notice
      && status === 'ready' && !!values[p] && values[p].state !== 'revoked']))});
  const publish = () => changed(snapshot());
  const current = g => !closed && g === generation;
  async function run(action) {
    const g = generation; busy = true; publish();
    try { await action(() => current(g)); }
    catch(error) {
      if (current(g)) {
        if (error?.status === 409) { notice = null; keys = {}; status = 'refresh'; }
        else if ([401,403,404,410,422].includes(error?.status)) { notice = null; status = 'blocked'; }
        else status = 'uncertain';
      }
    } finally { if (current(g)) {busy = false; publish();} }
  }
  return {
    snapshot,
    bind(value) {
      if (closed) return;
      if (intent?.intent_id === value?.intent_id && intent?.status === value?.status) return;
      generation++; busy = false;
      intent = value ? {intent_id:value.intent_id,status:value.status} : null;
      notice = null; values = {storage:null,ai_provider:null}; keys = {};
      status = intent ? 'unread' : 'empty'; publish();
    },
    close() {this.bind(null); closed = true; generation++;},
    async refresh() {
      if (!snapshot().canRead) return;
      const target = intent.intent_id;
      return run(async valid => {
        const data = await api.getRemoteUploadConsents(target);
        if (!valid()) return;
        if (!data || data.intent_id !== target || !text(data.notice_version)
            || !text(data.storage_notice) || !text(data.ai_provider_notice)) throw new Error('invalid_notice');
        const next = Object.fromEntries(consentPurposes.map(p => [p,
          data[p] === null ? null : checkedConsent(data[p],p,data.notice_version)]));
        if (notice && notice.version !== data.notice_version) keys = {};
        notice = {version:data.notice_version,storage:data.storage_notice,ai_provider:data.ai_provider_notice};
        values = next; status = 'ready';
      });
    },
    async grant(purpose, approved = false) {
      if (!approved || !snapshot().canGrant[purpose]) return;
      const target = intent.intent_id, version = notice.version;
      const key = keys[purpose] ??= newKey();
      return run(async valid => {
        const result = await api.grantRemoteUploadConsent(target,purpose,version,key);
        if (!valid()) return;
        values[purpose] = checkedConsent(result,purpose,version);
        delete keys[purpose]; status = 'ready';
      });
    },
    async revoke(purpose) {
      if (!snapshot().canRevoke[purpose]) return;
      const target = intent.intent_id, before = values[purpose], version = notice.version;
      return run(async valid => {
        const result = checkedConsent(await api.revokeRemoteUploadConsent(target,purpose,before.revision),purpose,version);
        if (!valid()) return;
        if (result.consent_id !== before.consent_id || result.revision !== before.revision
            || result.state !== 'revoked') throw new Error('invalid_revoke_response');
        values[purpose] = result; status = 'ready';
      });
    },
  };
}
