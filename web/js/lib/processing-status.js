// 공개 작업 조회만 사용하며 내부 처리 결과를 게시 완료로 해석하지 않는다.
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const isId = value => typeof value === 'string' && UUID.test(value);
const STATES = new Set(['queued', 'running', 'succeeded', 'failed', 'cancelled']);
export const processingMessages = {
  empty: '등록 준비를 저장한 뒤 기존 처리 작업을 조회할 수 있어요.',
  idle: '처리 작업을 조회해 주세요. 조회는 AI 호출이나 새 작업을 시작하지 않아요.',
  checking: '기존 처리 작업을 확인 중이에요.',
  queued: '처리 대기 중이에요. 아직 학습 준비 완료가 아니에요.',
  running: '서버에서 처리 중이에요. 세부 진행 단계는 아직 제공되지 않아요.',
  succeeded: '서버 작업이 성공으로 보고됐어요. 검색·학습 가능 여부는 별도로 확인해야 해요.',
  failed: '서버 작업이 실패했어요. 상세 사유는 아직 제공되지 않으며 자동 재접수하지 않아요.',
  cancelled: '서버 작업이 취소됐어요. 자동으로 다시 시작하지 않아요.',
  unavailable: '작업을 찾을 수 없거나 접근할 수 없어요. 새 작업을 자동 생성하지 않아요.',
  uncertain: '현재 처리 상태를 확인하지 못했어요. 같은 작업을 다시 조회해 주세요.',
  invalid: '서버 작업 응답을 확인할 수 없어요. 완료로 판단하지 않고 다시 조회해 주세요.',
  closed: '로그인 또는 화면이 바뀌었어요. 다시 열어 주세요.',
  quota: '이 계정이 만들 수 있는 학습 자료 수에 도달했어요. PDF는 보관됐지만 번역·검색 색인은 만들지 않아요.',
};
// 서버가 확정한 공개 코드만 표시한다. 내부 오류나 재시도 가능성을 추정하지 않는다.
const restrictionMessages = new Map([
  ['storage_consent_required', 'PDF 보관 동의가 필요해요. 현재 동의 안내와 상태를 확인해 주세요.'],
  ['ai_consent_required', '외부 AI 전송 동의가 필요해요. 현재 동의 안내와 상태를 확인해 주세요.'],
  ['budget_unavailable', '처리에 필요한 예산 또는 사용량 한도를 확보하지 못했어요. 같은 작업 상태를 다시 확인해 주세요.'],
  ['usage_pending', '사용량과 비용을 확인 중이에요. 미과금이나 환급이 확정된 상태가 아니며, 같은 작업 상태를 다시 확인해 주세요.'],
  ['provider_temporarily_unavailable', 'AI 제공자를 일시적으로 사용할 수 없어요. 같은 작업 상태를 다시 확인해 주세요.'],
  ['source_validation_failed', '원본 PDF 검증에 실패했어요. 입력 파일을 확인해 주세요.'],
  ['lease_expired', '서버 작업의 실행 권한이 만료됐어요. 같은 작업 상태를 다시 확인해 주세요.'],
  ['processing_failed', '처리를 완료하지 못했어요. 같은 작업 상태를 다시 확인해 주세요.'],
]);
export function processingGuidance(status, errorCode) {
  if (status !== 'failed') return processingMessages[status];
  const detail = restrictionMessages.get(errorCode);
  return detail ? `${detail} 자동으로 재접수하거나 AI 호출을 다시 시작하지 않아요.` : processingMessages.failed;
}
export function createProcessingStatus({api, changed = () => {}}) {
  let target = null, job = null, jobId = null, generation = 0, closed = false, busy = false, status = 'empty';
  const snapshot = () => ({status, job:job ? {...job} : null, message:processingGuidance(status, job?.error_code), busy, canRead:!!target && !busy && !closed});
  const publish = () => changed(snapshot());
  return {
    snapshot,
    bind(value) {
      if (closed) return;
      const next = value && ['intent_id','paper_id','version_id'].every(k => isId(value[k]))
        ? Object.fromEntries(['intent_id','paper_id','version_id'].map(k => [k,value[k]])) : null;
      if (JSON.stringify(next) === JSON.stringify(target)) return;
      generation++; target = next; job = null; jobId = null; busy = false; status = target ? 'idle' : 'empty'; publish();
    },
    close() {closed = true; generation++; target = job = jobId = null; busy = false; status = 'closed'; publish();},
    async refresh() {
      if (!snapshot().canRead) return;
      const g = generation, selected = {...target}, previousId = jobId;
      busy = true; job = null; status = 'checking'; publish();
      try {
        const value = await api.getRemoteProcessing(selected.intent_id);
        if (closed || g !== generation) return;
        if (!value || !isId(value.job_id) || !STATES.has(value.status)
            || value.paper_id !== selected.paper_id || value.version_id !== selected.version_id
            || (previousId && value.job_id !== previousId)) {
          status = 'invalid';
        } else {
          jobId = value.job_id;
          job = Object.fromEntries(['job_id','paper_id','version_id','status'].map(k => [k,value[k]]));
          if (value.status === 'failed' && restrictionMessages.has(value.error_code)) job.error_code = value.error_code;
          status = value.status;
        }
      } catch (error) {
        if (!closed && g === generation) {
          status = error?.code === 'remote_processing_quota_exceeded' ? 'quota'
            : [401,403,404].includes(error?.status) ? 'unavailable' : 'uncertain';
        }
      } finally {
        if (!closed && g === generation) {busy = false; publish();}
      }
    },
  };
}
