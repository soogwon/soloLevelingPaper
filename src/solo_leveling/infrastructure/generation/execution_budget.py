"""영속 실행의 전송 수·토큰·경과 시간 제한. 운영 정책 수치는 호출자가 고정한다."""
from dataclasses import dataclass
from solo_leveling.application.provider_dispatch import DispatchBudgetExceeded
from solo_leveling.interfaces.http.remote_executions import validate_execution_scope


@dataclass(frozen=True)
class ExecutionBudget:
    max_calls: int
    max_input_tokens: int
    max_output_tokens: int
    timeout_seconds: int

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in self.__dict__.values()):
            raise ValueError('execution_budget_invalid')


class BudgetedExecutionScope:
    def __init__(self, budget, request):
        if not isinstance(budget, ExecutionBudget):
            raise ValueError('execution_budget_required')
        self.budget, self.request = budget, request

    def __call__(self, conn, intent):
        # 동일 실행 잠금은 원장 INSERT까지 유지한다. 다른 lease에서도 소비량을 초기화하지 않는다.
        if not validate_execution_scope(conn, intent):
            return False
        row = conn.execute('''SELECT created_at + %s * interval '1 second' > clock_timestamp() AS within_time
            FROM server_remote_executions WHERE execution_id=%s AND owner_user_id=%s''',
            (self.budget.timeout_seconds, intent.execution_id, intent.owner_user_id)).fetchone()
        if not row or not row['within_time']:
            raise DispatchBudgetExceeded('time')
        usage = conn.execute('''SELECT count(*) AS calls,
            COALESCE(sum(GREATEST(reserved_input_tokens,COALESCE(actual_input_tokens,0))),0) AS input_tokens,
            COALESCE(sum(GREATEST(reserved_output_tokens,COALESCE(actual_output_tokens,0))),0) AS output_tokens
            FROM server_provider_attempts WHERE execution_id=%s AND owner_user_id=%s''',
            (intent.execution_id, intent.owner_user_id)).fetchone()
        if (usage['calls'] >= self.budget.max_calls
                or usage['input_tokens'] + self.request.input_token_upper_bound > self.budget.max_input_tokens
                or usage['output_tokens'] + self.request.max_output_tokens > self.budget.max_output_tokens):
            raise DispatchBudgetExceeded('calls_or_tokens')
        return True
