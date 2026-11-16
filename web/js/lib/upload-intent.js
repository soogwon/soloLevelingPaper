// 파일과 요청 키는 현재 화면 메모리에만 보관한다. 세션 변경 시 모두 폐기한다.
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const STATES = new Set(['created', 'uploading', 'verified', 'failed', 'expired']);
export const uploadMessages = {
  empty: 'PDF를 선택해 주세요.',
  hashing: '파일 지문을 계산 중이에요. 아직 서버로 보내지 않았어요.',
  selected: '파일을 선택했어요. 아래 전송 항목을 확인해 주세요.',
  invalid: '파일명·PDF 형식·크기를 확인해 주세요. 최대 50 MiB까지 선택할 수 있어요.',
  checking: '기존 요청을 확인 중이에요.',
  uncertain: '결과를 확인하지 못했어요. 이 화면에서 같은 요청을 다시 확인하세요. 자동으로 새 요청을 만들지 않아요.',
  created: '등록 준비가 저장됐어요. PDF 전송이나 학습 처리는 시작하지 않았어요.',
  uploading: '서버에서 업로드 중으로 표시돼요. 이 화면은 파일을 다시 전송하지 않아요.',
  verified: '서버의 PDF 검증이 완료됐어요. AI 전송 동의와 학습 처리 접수는 별도예요.',
  failed: '서버에서 업로드 실패로 표시돼요. 자동으로 다시 전송하지 않아요.',
  expired: '등록 준비가 만료됐어요. 파일을 다시 선택해야 새 요청을 만들 수 있어요.',
  blocked: '이 요청을 사용할 수 없어요. 로그인 상태와 선택한 파일을 확인해 주세요.',
  closed: '화면 또는 로그인 상태가 바뀌었어요. 다시 열어 주세요.',
};

export function createUploadIntent({ api, changed = () => {}, digest = async bytes =>
  [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(n => n.toString(16).padStart(2, '0')).join(''),
  newKey = () => crypto.randomUUID(), now = () => Date.now() }) {
  let generation = 0, closed = false, busy = false, metadata = null, key = null, intent = null;
  let status = 'empty';
  const snapshot = () => ({ status, busy, file: metadata ? {name:metadata.file_name, size:metadata.file_size} : null,
    intent: intent ? {...intent} : null, canCreate: !closed && !busy && !!metadata && !intent && ['selected','uncertain'].includes(status),
    canRead: !closed && !busy && !!intent });
  const publish = () => changed(snapshot());
  const current = g => !closed && g === generation;
  function accept(value) {
    if (!value || !['intent_id','paper_id','version_id'].every(k => UUID.test(value[k]))
        || !STATES.has(value.status) || !Number.isFinite(Date.parse(value.expires_at))
        || (intent && ['intent_id','paper_id','version_id'].some(k => intent[k] !== value[k]))) throw new Error('invalid_response');
    intent = Object.fromEntries(['intent_id','paper_id','version_id','status','expires_at'].map(k => [k,value[k]]));
    status = ['created','uploading'].includes(value.status) && Date.parse(value.expires_at) <= now() ? 'expired' : value.status;
  }
  return {
    snapshot,
    close() { closed = true; generation++; metadata = key = intent = null; busy = false; status = 'closed'; publish(); },
    async select(file) {
      if (closed) return;
      const g = ++generation;
      metadata = key = intent = null; busy = false; status = 'empty';
      if (!file) { publish(); return; }
      if (typeof file.name !== 'string' || file.name !== file.name.trim() || file.name.length > 255
          || /[\x00-\x1f/\\:]/.test(file.name) || !/\.pdf$/i.test(file.name)
          || !['', 'application/pdf'].includes(file.type) || !Number.isSafeInteger(file.size)
          || file.size <= 0 || file.size > 50*1024*1024) { status = 'invalid'; publish(); return; }
      busy = true; status = 'hashing'; publish();
      try {
        const bytes = await file.arrayBuffer();
        if (!current(g)) return;
        if (bytes.byteLength !== file.size || new TextDecoder().decode(bytes.slice(0,5)) !== '%PDF-') throw new Error('invalid_pdf');
        const sha256 = await digest(bytes);
        if (!current(g)) return;
        if (!/^[0-9a-f]{64}$/.test(sha256)) throw new Error('invalid_hash');
        metadata = {file_name:file.name, file_size:file.size, content_type:'application/pdf', sha256};
        key = newKey(); status = 'selected';
      } catch { if (current(g)) status = 'invalid'; }
      finally { if (current(g)) { busy = false; publish(); } }
    },
    async run(approved = false) {
      if (closed || busy || (!intent && (!approved || !snapshot().canCreate))) return;
      const g = generation; busy = true; status = 'checking'; publish();
      try {
        const result = intent ? await api.getRemoteUploadIntent(intent.intent_id)
          : await api.createRemoteUploadIntent({...metadata}, key);
        if (current(g)) accept(result);
      } catch (error) {
        if (current(g)) status = error?.status === 410 ? 'expired'
          : [401,403,404,409,422].includes(error?.status) ? 'blocked' : 'uncertain';
      } finally { if (current(g)) { busy = false; publish(); } }
    },
  };
}
