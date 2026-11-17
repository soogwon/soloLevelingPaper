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
