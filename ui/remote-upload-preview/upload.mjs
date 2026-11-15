// #393 검토용 대역. 파일 바이트 읽기·업로드·동의 저장·작업 접수는 하지 않는다.
const LIMIT = 50 * 1024 * 1024;
const MESSAGES = Object.freeze({
  empty: 'PDF를 선택하세요.',
  selected: '파일을 선택했습니다. 아직 서버로 전송하지 않았습니다.',
  invalid: '이름·PDF 형식·크기를 확인하세요. 최대 50 MiB까지 선택할 수 있습니다.',
  created: '업로드 의도가 생성되었습니다. 업로드 완료나 처리 접수를 뜻하지 않습니다.',
  uploading: '업로드 상태입니다. 서버 검증 완료를 기다려야 합니다.',
  verified: '서버 검증 완료 상태입니다. 외부 AI 전송 동의와 처리 접수는 별도입니다.',
  failed: '업로드에 실패했습니다. 같은 의도의 상태를 확인하세요. 자동으로 재업로드하지 않습니다.',
  expired: '업로드 의도가 만료되었습니다. 기존 의도를 자동으로 다시 제출하지 않습니다.',
  unavailable: '상태를 확인하지 못했습니다. 같은 의도의 상태를 다시 확인하세요.',
});
const JOB_STATES = new Set(['queued', 'running', 'succeeded', 'failed', 'cancelled']);
const JOB_MESSAGES = Object.freeze({
  none: '아직 처리 접수 결과가 없습니다. PDF 검증 완료는 AI 전송 승인이나 작업 접수를 뜻하지 않습니다.',
  checking: '같은 업로드의 기존 작업 상태를 확인 중입니다. 다시 접수하지 않습니다.',
  queued: '기존 작업이 접수되어 대기 중입니다.',
  running: '기존 작업을 처리 중입니다.',
  succeeded: '기존 처리 작업이 완료되었습니다.',
  failed: '기존 작업이 실패했습니다. 자동으로 다시 접수하지 않습니다.',
  cancelled: '기존 작업이 취소되었습니다. 이미 전송된 요청의 정산은 별도입니다.',
  unavailable: '접수 여부를 확인하지 못했습니다. 같은 업로드의 기존 작업을 다시 조회하세요.',
});
const SERVER_STATES = new Set(['created', 'uploading', 'verified', 'failed', 'expired']);
const escape = value => String(value).replace(/[&<>"']/g, char => ({
  '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;',
}[char]));

export function createPreview() {
  let generation = 0;
  let file = null;
  let status = 'empty';
  let intentId = null;
  let admissionStatus = 'none';
  let job = null;
  let activeLookup = null;
  function clearAdmission() {admissionStatus='none'; job=null; activeLookup=null;}

  function snapshot() { return {generation, file: file ? {...file} : null, status, admissionStatus, job:job ? {...job} : null}; }
  return {
    select(candidate) {
      generation += 1;
      file = null; intentId = null; status = 'empty'; clearAdmission();
      if (candidate == null) return snapshot();
      if (typeof candidate.name !== 'string' || !candidate.name.trim()
          || candidate.name !== candidate.name.trim() || candidate.name.length > 255
          || /[\x00-\x1f/\\]/.test(candidate.name) || !/\.pdf$/i.test(candidate.name)
          || candidate.type !== 'application/pdf' || !Number.isSafeInteger(candidate.size)
          || candidate.size <= 0 || candidate.size > LIMIT) {
        status = 'invalid'; return snapshot();
      }
      file = {name:candidate.name, size:candidate.size}; status = 'selected';
      return snapshot();
    },
    receive(expectedGeneration, response, now = Date.now()) {
      // 파일 교체/취소 후 도착한 이전 선택의 응답은 반영하지 않는다.
      if (expectedGeneration !== generation || file === null) return snapshot();
      if (!response || typeof response.intentId !== 'string' || !response.intentId.trim()
          || !SERVER_STATES.has(response.status) || !Number.isFinite(response.expiresAt)
          || !Number.isFinite(now) || (intentId !== null && intentId !== response.intentId)) {
        status = 'unavailable'; clearAdmission(); return snapshot();
      }
      intentId = response.intentId;
      status = ['created', 'uploading'].includes(response.status) && response.expiresAt <= now
        ? 'expired' : response.status;
      if (status !== 'verified') clearAdmission();
      return snapshot();
    },
    beginAdmissionLookup() {
      if (status !== 'verified' || !intentId) return null;
      // 내부 조회 표식이며 승인·접수 키가 아니다. 공개 API 계약으로 사용하지 않는다.
      activeLookup = Object.freeze({generation, intentId});
      admissionStatus = 'checking';
      return activeLookup;
    },
    receiveAdmission(ticket, response) {
      if (!ticket || ticket !== activeLookup || ticket.generation !== generation || status !== 'verified') return snapshot();
      activeLookup = null;
      const candidate = response?.job;
      if (response?.intentId !== intentId || !candidate || !JOB_STATES.has(candidate.status)
          || !['job_id','paper_id','server_version_id'].every(k => typeof candidate[k] === 'string' && candidate[k].trim())
          || (job && ['job_id','paper_id','server_version_id'].some(k => candidate[k] !== job[k]))) {
        admissionStatus = 'unavailable'; return snapshot();
      }
      job = {job_id:candidate.job_id, paper_id:candidate.paper_id,
        server_version_id:candidate.server_version_id, status:candidate.status};
      admissionStatus = candidate.status;
      return snapshot();
    },
    snapshot,
  };
}

export function renderPreview(view) {
  const state = Object.hasOwn(MESSAGES, view?.status) ? view.status : 'unavailable';
  const file = view?.file;
  return `<section aria-labelledby="upload-title"><h1 id="upload-title">PDF 등록 미리보기</h1>
    <p>대역 미리보기입니다. 파일을 전송하거나 작업을 접수하지 않습니다.</p>
    ${file ? `<dl><dt>파일명</dt><dd>${escape(file.name)}</dd><dt>크기</dt><dd>${escape(file.size)} bytes</dd></dl>` : ''}
    <p role="status">${MESSAGES[state]}</p>
    <h2>처리 작업 상태</h2>
    <p role="status">${JOB_MESSAGES[Object.hasOwn(JOB_MESSAGES, view?.admissionStatus) ? view.admissionStatus : 'unavailable']}</p>
    ${view?.job?.job_id ? `<p>기존 작업 ID: ${escape(view.job.job_id)}</p>` : ''}
    <p>서버 PDF 보관 승인과 외부 AI 전송 승인은 별도로 확인해야 합니다.</p>
    <button type="button" disabled>서버 보관 동의 확인</button>
    <button type="button" disabled>외부 AI 전송 동의 확인</button>
    <button type="button" disabled>처리 접수</button></section>`;
}
