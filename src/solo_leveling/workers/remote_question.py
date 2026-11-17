"""정책이 고정된 질문 실행을 한 번 claim한다. 생성·저장은 기존 영속 서비스가 맡는다."""
from threading import Event, Thread

from solo_leveling.application.provider_dispatch import DispatchBudgetExceeded, DispatchError


class RemoteQuestionRunner:
    def __init__(self, store, execute):
        self.store, self.execute = store, execute

    def run(self, owner, execution_id):
        policy = self.store.policy(owner, execution_id)
        if policy is None:
            raise ValueError('remote_execution_policy_required')
        if self.store.expire(owner, execution_id):
            return 'expired'
        lease_seconds = min(policy.timeout_seconds, max(3, policy.stage_timeout_seconds))
        intent = self.store.claim(owner, execution_id, lease_seconds=lease_seconds)
        if intent is None:
            return 'not_claimed'
        return self._execute_claimed(intent, policy)

    def run_once(self):
        selected = self.store.claim_next_question()
        if selected is None:
            return None
        status, intent, policy = selected
        if status != 'claimed':
            return status
        return self._execute_claimed(intent, policy)

    def _execute_claimed(self, intent, policy):
        lease_seconds = min(policy.timeout_seconds, max(3, policy.stage_timeout_seconds))
        stop, lost = Event(), Event()
        def heartbeat():
            while not stop.wait(max(.1, lease_seconds / 3)):
                try:
                    if self.store.heartbeat(intent, lease_seconds=lease_seconds):
                        continue
                except Exception:
                    pass
                lost.set()
                return
        thread = Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            # 실행 factory는 정책 timeout과 영속 전송 gate를 모든 단계에 적용해야 한다.
            # 메모리 결과를 반환하지 않는다. 성공 응답은 별도 읽기 포트로 재조회한다.
            self.execute(intent, policy, lambda: not lost.is_set())
            return 'finished'
        except DispatchBudgetExceeded as error:
            self.store.fail(intent, 'deadline_exceeded' if str(error) == 'DISPATCH_TIME_BUDGET_EXCEEDED' else 'budget_exhausted')
            return 'limited'
        except DispatchError:
            self.store.fail(intent, 'provider_failed')
            return 'failed'
        except Exception:
            self.store.fail(intent, 'internal_failed')
            return 'failed'
        finally:
            stop.set()
            thread.join(timeout=10)


class PersistedQuestionExecutor:
    """runner의 현재 lease로만 입력을 복구하여 기존 생성·원자 저장 조립체에 넘긴다."""

    def __init__(self, store, execute):
        self.store, self.execute = store, execute

    def __call__(self, intent, policy, allowed):
        if not allowed():
            raise DispatchError('DISPATCH_NOT_AUTHORIZED')
        request = self.store.question_input(intent)
        if request is None or not allowed():
            raise DispatchError('DISPATCH_NOT_AUTHORIZED')
        return self.execute(intent, policy, allowed, request)


def run_question_queue(runner, stop, *, max_jobs, idle_seconds=5.0):
    """중지와 처리 상한을 지키며 기존 질문만 소비한다. 오류 뒤 자동 재전송하지 않는다."""
    from math import isfinite
    from .processing_queue import QueueRunResult
    if (not callable(getattr(runner,'run_once',None)) or not callable(getattr(stop,'wait',None))
            or not callable(getattr(stop,'is_set',None)) or type(max_jobs) is not int
            or not 1 <= max_jobs <= 10000 or type(idle_seconds) not in (int,float)
            or not isfinite(idle_seconds) or not 0.1 <= idle_seconds <= 300):
        raise ValueError('question_queue_config_invalid')
    processed = 0
    while processed < max_jobs:
        if stop.is_set():
            return QueueRunResult('stopped',processed)
        try:
            status = runner.run_once()
        except Exception:
            return QueueRunResult('failed',processed)
        if status is None:
            if stop.wait(idle_seconds):
                return QueueRunResult('stopped',processed)
            continue
        processed += 1
        if status not in ('finished','expired','blocked'):
            return QueueRunResult('failed',processed)
    return QueueRunResult('limit_reached',processed)
