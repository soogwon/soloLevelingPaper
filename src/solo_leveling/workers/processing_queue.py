"""이미 동의·예산 gate로 조립된 worker의 제한적 큐 소비. 배포 기본값을 바꾸지 않는다."""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class QueueRunResult:
    status: str
    processed: int


def run_processing_queue(worker, stop, *, max_jobs: int, idle_seconds: float = 5.0, settle_failed=None):
    """새 접수·재시도 설정·예산을 만들지 않고 기존 claim/lease 경계를 사용한다.

    stop은 다음 claim을 막는다. 진행 중인 요청의 물리 취소나 미과금은 보장하지 않는다.
    영속 중지·철회·삭제는 기존 worker의 단계별 gate에서 별도로 검사한다.
    """
    if (not callable(getattr(worker,'run_once',None)) or not callable(getattr(stop,'wait',None))
            or not callable(getattr(stop,'is_set',None)) or type(max_jobs) is not int
            or not 1 <= max_jobs <= 10000 or type(idle_seconds) not in (int,float)
            or not isfinite(idle_seconds) or not 0.1 <= idle_seconds <= 300):
        raise ValueError('processing_queue_config_invalid')
    processed = 0
    while processed < max_jobs:
        if stop.is_set():
            return QueueRunResult('stopped',processed)
        try:
            result = worker.run_once()
        except Exception:
            # 데모는 실패한 실행을 먼저 종결한 경우에만 다음 작업을 가져온다.
            if settle_failed is None or not settle_failed():
                return QueueRunResult('failed',processed)
            processed += 1
            continue
        if result is None:
            if stop.wait(idle_seconds):
                return QueueRunResult('stopped',processed)
            continue
        processed += 1
        if getattr(result,'published',None) is None:
            if settle_failed is None or not settle_failed():
                return QueueRunResult('not_published',processed)
    return QueueRunResult('limit_reached',processed)
