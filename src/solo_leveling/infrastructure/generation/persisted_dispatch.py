"""원격 Responses 전송을 영속 원장과 정확한 실행 scope 검사에 연결한다."""
from solo_leveling.application.provider_dispatch import ProviderDispatch
from solo_leveling.interfaces.http.dispatch_ledger import PostgresQuestionDispatchLedger
from solo_leveling.interfaces.http.remote_executions import validate_execution_scope, PostgresRemoteExecutionStore
from .gated_responses import GatedResponsesTransport
from .execution_budget import ExecutionBudget, BudgetedExecutionScope


def create_persisted_responses_transport(pool, intent, policy_version, window_key, prepare, *, post=None, execution_budget=None, validate_limits=None):
    """prepare는 안전한 토큰 상한·영속 전송 키를 공급한다. 임의 정책·키를 생성하지 않는다.

    반환 transport를 생성기와 의미 검증기에 함께 주입한다. post 대역을 생략한 실제 전송에는
    별도의 외부 호출 승인이 필요하다. 기존 로컬 생성기의 기본 transport는 바꾸지 않는다.
    """
    if not callable(prepare):
        raise ValueError('dispatch_prepare_required')
    if execution_budget is not None and not isinstance(execution_budget, ExecutionBudget):
        raise ValueError('execution_budget_invalid')

    stored_policy = PostgresRemoteExecutionStore(pool).policy(intent.owner_user_id, intent.execution_id)
    if stored_policy is not None:
        execution_budget = ExecutionBudget(stored_policy.max_calls,stored_policy.max_input_tokens,
                                           stored_policy.max_output_tokens,stored_policy.timeout_seconds)

    class Ledger(PostgresQuestionDispatchLedger):
        def authorize(self, request):
            # 전송마다 지역 adapter를 만들어 동시 호출 간 request를 공유하지 않는다.
            validator = validate_execution_scope if execution_budget is None else BudgetedExecutionScope(execution_budget, request)
            return PostgresQuestionDispatchLedger(pool, policy_version, window_key, validator,
                validate_limits=validate_limits).authorize(request)

    ledger = Ledger(pool, policy_version, window_key, validate_execution_scope, validate_limits=validate_limits)
    transport = GatedResponsesTransport(ProviderDispatch(ledger, intent), prepare, post=post)
    if stored_policy is None:
        return transport

    class PolicyTransport:
        dispatch = transport.dispatch
        def send(self, settings, payload, stage):
            class LimitedSettings:
                timeout_seconds = min(settings.timeout_seconds, stored_policy.stage_timeout_seconds)
                def __getattr__(self, name):
                    return getattr(settings, name)
            return transport.send(LimitedSettings(), payload, stage)
    return PolicyTransport()
