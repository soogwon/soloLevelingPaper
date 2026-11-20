"""명시적으로 선택한 요약 1건만 영속 전송 gate로 생성한다. 자동 등록은 하지 않는다."""
from contextlib import nullcontext
from types import SimpleNamespace

from solo_leveling.application.provider_dispatch import DispatchError
from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.application.summary.remote_contract import OPERATION
from solo_leveling.application.summary.prompt import summary_payload
from solo_leveling.infrastructure.generation.gated_responses import PreparedProviderAttempt
from solo_leveling.infrastructure.generation.openai_generator import _response_text
from solo_leveling.infrastructure.generation.persisted_dispatch import create_persisted_responses_transport
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore, _question_consent
from solo_leveling.interfaces.http.remote_summary_results import PostgresSummaryResults
from .remote_question import RemoteQuestionRunner


class PersistedSummaryExecutor:
    def __init__(self, pool, *, settings, token_upper_bound, window_key, post=None):
        if not callable(token_upper_bound) or not isinstance(window_key, str) or not window_key.strip():
            raise ValueError('remote_summary_executor_invalid')
        self.pool, self.settings, self.estimate = pool, settings, token_upper_bound
        self.window, self.post = window_key, post

    def __call__(self, intent, policy, allowed):
        results = PostgresSummaryResults(self.pool)
        request = results.input(intent)
        if request is None or not allowed():
            raise DispatchError('DISPATCH_NOT_AUTHORIZED')
        payload = summary_payload(request, self.settings, policy)

        def prepare(stage, body, digest):
            if stage != 'generation' or not allowed():
                raise DispatchError('DISPATCH_NOT_AUTHORIZED')
            upper = self.estimate(self.settings.model, body)
            if type(upper) is not int or not 1 <= upper <= policy.max_input_tokens:
                raise ValueError('remote_summary_token_limit')
            return PreparedProviderAttempt('summary-generation-v1', digest, upper)

        transport = create_persisted_responses_transport(self.pool, intent, policy.version, self.window,
                                                         prepare, post=self.post)
        envelope = transport.send(self.settings, payload, 'generation')
        if not allowed():
            raise DispatchError('DISPATCH_NOT_AUTHORIZED')
        try:
            from solo_leveling.application.summary.remote_contract import _load
            decoded = _load(_response_text(envelope))
            if type(decoded) is not dict or set(decoded) != {'items'}:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError('remote_summary_response_invalid') from None
        return results.complete(intent, decoded['items'])


class RemoteSummaryRunner:
    def __init__(self, pool, execute, *, policy_version=None, window_key=None):
        self.pool = pool
        self.policy, self.window = policy_version, window_key
        self.runner = RemoteQuestionRunner(PostgresRemoteExecutionStore(pool), execute)

    def _claim(self, conn, row):
        owner, execution_id = (row['owner_user_id'], row['execution_id']) if row is not None else (None, None)
        if row is None:
            return 'not_found'
        scoped = PostgresRemoteExecutionStore(SimpleNamespace(connection=lambda: nullcontext(conn)))
        if row['state'] == 'cancel_requested':
            scoped.confirm_cancel(owner, execution_id)
            return 'cancelled'
        if row['state'] not in ('queued', 'running'):
            return 'not_claimed'
        if row['state'] == 'running' and conn.execute(
            'SELECT %s>clock_timestamp() AS active', (row['lease_expires_at'],)).fetchone()['active']:
            return 'not_claimed'
        if scoped.expire(owner, execution_id):
            return 'expired'
        paper = conn.execute("""SELECT 1 FROM server_execution_contexts c JOIN server_papers p
            ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
            WHERE c.owner_user_id=%s AND c.context_id=%s AND p.deleted_at IS NULL
            FOR UPDATE OF p""", (owner, row['context_id'])).fetchone()
        consent = _question_consent(conn, owner, row['storage_consent_id'], row['ai_consent_id'],
                                    row['input_hash'], row['notice_version'])
        attempted = conn.execute('SELECT 1 FROM server_provider_attempts WHERE owner_user_id=%s AND execution_id=%s LIMIT 1',
                                 (owner, execution_id)).fetchone()
        if paper is None or consent is None or attempted is not None:
            conn.execute("""UPDATE server_remote_executions SET state='failed',terminal_reason=%s,
                lease_token=NULL,lease_expires_at=NULL,finished_at=clock_timestamp(),updated_at=clock_timestamp()
                WHERE owner_user_id=%s AND execution_id=%s""",
                ('provider_failed' if attempted is not None else 'scope_revoked', owner, execution_id))
            return 'blocked'
        policy = RemoteExecutionPolicy(**row['policy_snapshot'])
        intent = scoped.claim(owner, execution_id,
            lease_seconds=min(policy.timeout_seconds, max(3, policy.stage_timeout_seconds)))
        return (intent, policy) if intent is not None else 'not_claimed'

    def _finish(self, selected):
        if isinstance(selected, str):
            return selected
        intent, policy = selected
        return self.runner._execute_claimed(intent, policy)

    def run(self, owner, execution_id):
        with self.pool.connection() as conn:
            row = conn.execute("""SELECT e.* FROM server_remote_executions e
                JOIN server_summary_inputs s ON s.owner_user_id=e.owner_user_id AND s.execution_id=e.execution_id
                WHERE e.owner_user_id=%s AND e.execution_id=%s AND e.operation=%s
                FOR UPDATE OF e""", (owner, execution_id, OPERATION)).fetchone()
            selected = self._claim(conn, row)
        return self._finish(selected)

    def run_once(self):
        if self.policy is None or self.window is None:
            raise ValueError('remote_summary_queue_scope_required')
        with self.pool.connection() as conn:
            row = conn.execute("""SELECT e.* FROM server_remote_executions e
                JOIN server_summary_inputs s ON s.owner_user_id=e.owner_user_id AND s.execution_id=e.execution_id
                JOIN server_question_budget_reservations r ON r.owner_user_id=e.owner_user_id AND r.execution_id=e.execution_id
                WHERE e.operation=%s AND e.policy_snapshot->>'version'=%s AND r.policy_version=%s AND r.window_key=%s
                  AND (e.state IN ('queued','cancel_requested') OR
                       e.state='running' AND e.lease_expires_at<=clock_timestamp())
                ORDER BY e.created_at,e.execution_id FOR UPDATE OF e SKIP LOCKED LIMIT 1""",
                (OPERATION, self.policy, self.policy, self.window)).fetchone()
            if row is None:
                return None
            selected = self._claim(conn, row)
        return self._finish(selected)
