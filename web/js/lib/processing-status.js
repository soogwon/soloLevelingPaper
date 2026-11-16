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
};
export function createProcessingStatus({api, changed = () => {}}) {
  let target = null, job = null, jobId = null, generation = 0, closed = false, busy = false, status = 'empty';
  const snapshot = () => ({status, job:job ? {...job} : null, busy, canRead:!!target && !busy && !closed});
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
          status = value.status;
        }
      } catch (error) {
        if (!closed && g === generation) status = [401,403,404].includes(error?.status) ? 'unavailable' : 'uncertain';
      } finally {
        if (!closed && g === generation) {busy = false; publish();}
      }
    },
  };
}
