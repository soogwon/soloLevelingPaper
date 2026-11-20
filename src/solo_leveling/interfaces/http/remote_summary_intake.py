"""요약 전용 입력·동의를 확인하고 기존 영속 실행·예산 원장에 원자 접수한다."""
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import UUID
import json

from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.application.remote_replay import ExecutionLookup
from solo_leveling.application.summary.remote_contract import NOTICE_VERSION, OPERATION, RemoteSummaryInput
from .remote_executions import PostgresRemoteExecutionStore, RemoteExecutionConflict, _question_consent
from .remote_summary_sources import PostgresSummaryPreparation


class SummaryAdmissionDenied(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


SUMMARY_ADMISSION_MESSAGES = {
    'summary_consent_required': '요약 저장과 AI 전송 동의를 웹에서 다시 확인하세요.',
    'summary_intake_closed': '현재 새 요약 접수가 중지돼 있어요.',
    'summary_budget_unavailable': '현재 계정 또는 서비스의 예산으로 새 요약을 접수할 수 없어요.',
    'summary_review_required': '요약 검토 범위와 유효 기간을 웹에서 다시 확인하세요.',
}


class PostgresSummaryIntake:
    def __init__(self, pool, *, settings):
        keys = {'provider', 'model', 'prompt_version', 'language', 'budget_policy_version'}
        if (type(settings) is not dict or set(settings) != keys
                or any(not isinstance(v, str) or not v.strip() for v in settings.values())):
            raise ValueError('remote_summary_settings_invalid')
        self.pool = pool
        self.settings = dict(settings)

    def submit(self, owner, request_key, request, storage_id, ai_id, *, policy, window_key, report_denial=False):
        """인증 owner와 서버 정책을 전달한다. 전송·생성·자동 재시도는 수행하지 않는다."""
        if (type(request) is not RemoteSummaryInput or type(policy) is not RemoteExecutionPolicy
                or policy.max_calls != 1 or not isinstance(window_key, str)
                or not window_key.strip() or len(window_key) > 200):
            raise ValueError('remote_summary_admission_invalid')
        key = str(UUID(request_key))
        binding, _, config = request.unpack()
        if owner != binding.owner_user_id:
            return None
        if policy.version != config['budget_policy_version'] or policy.version != self.settings['budget_policy_version']:
            raise ValueError('remote_summary_policy_mismatch')
        lookup = ExecutionLookup(owner, OPERATION, key, request.input_hash, binding.context_id)
        try:
            with self.pool.connection() as conn:
                scoped = SimpleNamespace(connection=lambda: nullcontext(conn))
                store = PostgresRemoteExecutionStore(scoped)
                conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                             (f'{owner}\x1f{OPERATION}\x1f{key}',))
                # 재조회는 worker와 동일하게 실행 행을 부모 논문보다 먼저 잠근다.
                conn.execute('''SELECT execution_id FROM server_remote_executions
                    WHERE owner_user_id=%s AND operation=%s AND request_key=%s FOR UPDATE''',
                    (owner, OPERATION, key)).fetchone()
                # 전체 입력 대조와 예산 예약 전에 삭제와 같은 부모 잠금을 잡는다.
                parent = conn.execute('''SELECT p.paper_id FROM server_execution_contexts c
                    JOIN server_papers p ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                    WHERE c.owner_user_id=%s AND c.context_id=%s AND p.deleted_at IS NULL
                    FOR UPDATE OF p''', (owner, binding.context_id)).fetchone()
                if parent is None:
                    raise SummaryAdmissionDenied('summary_review_required')
                existing = store.lookup(owner, OPERATION, key)
                if existing is None:
                    preparation = PostgresSummaryPreparation(scoped, **self.settings)
                    current = preparation.prepare(owner, binding.context_id,
                                                  scope=config['scope'], section_id=config['section_id'], policy=policy)
                    if current is None or current.input_hash != request.input_hash:
                        raise SummaryAdmissionDenied('summary_review_required')
                    # DB 원장의 목적 이름은 공통 storage/ai_provider이다. 요약 전용 해시와 고지로 분리한다.
                    if _question_consent(conn, owner, storage_id, ai_id, request.input_hash, NOTICE_VERSION) is None:
                        raise SummaryAdmissionDenied('summary_consent_required')
                    control = conn.execute('''SELECT intake_enabled FROM server_dispatch_controls
                        WHERE control_id='provider_dispatch' FOR SHARE''').fetchone()
                    if control is None or not control['intake_enabled']:
                        raise SummaryAdmissionDenied('summary_intake_closed')
                saved = store.create(lookup, storage_id, ai_id, NOTICE_VERSION, policy=policy)
                if saved is None:
                    raise SummaryAdmissionDenied('summary_review_required')
                if existing is not None:
                    row = conn.execute('''SELECT s.input_snapshot,r.policy_version,r.window_key,e.policy_snapshot
                        FROM server_summary_inputs s JOIN server_question_budget_reservations r
                        ON r.owner_user_id=s.owner_user_id AND r.execution_id=s.execution_id
                        JOIN server_remote_executions e ON e.owner_user_id=s.owner_user_id
                          AND e.execution_id=s.execution_id
                        WHERE s.owner_user_id=%s AND s.execution_id=%s''', (owner, saved.execution_id)).fetchone()
                    if (row is None or row['policy_version'] != policy.version or row['window_key'] != window_key or row['policy_snapshot'] != policy.snapshot()
                            or RemoteSummaryInput(json.dumps(row['input_snapshot'])).input_hash != request.input_hash):
                        raise RemoteExecutionConflict('remote_summary_admission_conflict')
                    return saved
                conn.execute('''INSERT INTO server_summary_inputs(owner_user_id,execution_id,input_snapshot)
                    VALUES (%s,%s,%s::jsonb)''', (owner, saved.execution_id, request.encoded))
                limits = conn.execute('''SELECT * FROM server_dispatch_budget_limits
                    WHERE policy_version=%s AND window_key=%s AND
                     ((scope_type='service' AND scope_id='service') OR (scope_type='owner' AND scope_id=%s))
                    ORDER BY scope_type,scope_id FOR UPDATE''', (policy.version, window_key, owner)).fetchall()
                if ({r['scope_type'] for r in limits} != {'owner', 'service'} or any(
                        r['reserved_input_tokens'] + r['consumed_input_tokens'] + policy.max_input_tokens > r['max_input_tokens']
                        or r['reserved_output_tokens'] + r['consumed_output_tokens'] + policy.max_output_tokens > r['max_output_tokens']
                        for r in limits)):
                    raise SummaryAdmissionDenied('summary_budget_unavailable')
                # 기존 전체 실행 예산 원장을 공유한다. 물리 전송 예약은 이 예약 안에서만 소비한다.
                conn.execute('''INSERT INTO server_question_budget_reservations
                    (owner_user_id,execution_id,policy_version,window_key,remaining_input_tokens,remaining_output_tokens)
                    VALUES (%s,%s,%s,%s,%s,%s)''', (owner, saved.execution_id, policy.version, window_key,
                        policy.max_input_tokens, policy.max_output_tokens))
                conn.execute('''UPDATE server_dispatch_budget_limits SET
                    reserved_input_tokens=reserved_input_tokens+%s,reserved_output_tokens=reserved_output_tokens+%s
                    WHERE policy_version=%s AND window_key=%s AND
                     ((scope_type='service' AND scope_id='service') OR (scope_type='owner' AND scope_id=%s))''',
                    (policy.max_input_tokens, policy.max_output_tokens, policy.version, window_key, owner))
                return saved
        except SummaryAdmissionDenied:
            if report_denial:
                raise
            return None
