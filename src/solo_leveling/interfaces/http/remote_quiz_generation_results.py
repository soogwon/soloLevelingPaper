"""퀴즈 결과를 유효한 실행 세대에만 원자 저장하고 같은 요청으로 읽는다."""
from contextlib import nullcontext
import json
from types import SimpleNamespace
from uuid import uuid5, NAMESPACE_URL

from solo_leveling.application.quiz.remote_contract import (
    NOTICE as NOTICE_VERSION, RemoteQuizGenerationInput, validate_generated_questions,
)
from .remote_quiz_intake import OPERATION
from .remote_quiz import PostgresRemoteQuiz
from dataclasses import asdict
from .remote_executions import (
    PostgresRemoteExecutionStore, RemoteExecutionLeaseLost, validate_execution_scope, _question_consent,
)


class PostgresQuizGenerationResults:
    def __init__(self, pool):
        self.pool = pool

    def input(self, intent):
        with self.pool.connection() as conn:
            if not validate_execution_scope(conn, intent):
                return None
            if _question_consent(conn, intent.owner_user_id, intent.storage_consent_id,
                                 intent.ai_consent_id, intent.input_hash, NOTICE_VERSION) is None:
                return None
            row = conn.execute("""SELECT s.input_snapshot FROM server_quiz_generation_inputs s
                JOIN server_remote_executions e ON e.owner_user_id=s.owner_user_id AND e.execution_id=s.execution_id
                WHERE s.owner_user_id=%s AND s.execution_id=%s AND e.operation=%s""",
                (intent.owner_user_id, intent.execution_id, OPERATION)).fetchone()
            if row is None:
                return None
            request = RemoteQuizGenerationInput(json.dumps(row['input_snapshot']))
            if request.input_hash != intent.input_hash:
                raise ValueError('remote_quiz_generation_input_invalid')
            return request

    def complete(self, intent, items):
        with self.pool.connection() as conn:
            if not validate_execution_scope(conn, intent):
                raise RemoteExecutionLeaseLost('remote_quiz_generation_lease_lost')
            if _question_consent(conn, intent.owner_user_id, intent.storage_consent_id,
                                 intent.ai_consent_id, intent.input_hash, NOTICE_VERSION) is None:
                raise RemoteExecutionLeaseLost('remote_quiz_generation_consent_revoked')
            row = conn.execute('''SELECT s.input_snapshot,clock_timestamp() AS generated_at
                FROM server_quiz_generation_inputs s JOIN server_remote_executions e
                ON e.owner_user_id=s.owner_user_id AND e.execution_id=s.execution_id
                WHERE s.owner_user_id=%s AND s.execution_id=%s AND e.operation=%s''',
                (intent.owner_user_id, intent.execution_id, OPERATION)).fetchone()
            if row is None:
                raise ValueError('remote_quiz_generation_execution_invalid')
            request = RemoteQuizGenerationInput(json.dumps(row['input_snapshot']))
            if request.input_hash != intent.input_hash:
                raise ValueError('remote_quiz_generation_input_invalid')
            attempts = conn.execute('''SELECT status,stage,lease_token FROM server_provider_attempts
                WHERE owner_user_id=%s AND execution_id=%s FOR SHARE''',
                (intent.owner_user_id, intent.execution_id)).fetchall()
            if (len(attempts) != 1 or attempts[0]['status'] != 'observed'
                    or attempts[0]['stage'] != 'generation' or attempts[0]['lease_token'] != intent.lease_token):
                raise ValueError('remote_quiz_generation_usage_unconfirmed')
            binding, _, config = request.unpack()
            questions = validate_generated_questions(request, items)
            scoped = SimpleNamespace(connection=lambda: nullcontext(conn))
            quiz = PostgresRemoteQuiz(scoped)
            current = quiz._binding(conn, intent.owner_user_id, binding.context_id, write=True)
            if current is None or asdict(current) != asdict(binding):
                raise RemoteExecutionLeaseLost('remote_quiz_binding_changed')
            quiz_id = str(uuid5(NAMESPACE_URL, 'remote-quiz:' + intent.owner_user_id + ':' + intent.execution_id))
            payload = quiz.store_reviewed(intent.owner_user_id, binding.context_id, quiz_id, config['step'], questions)
            if payload is None:
                raise RemoteExecutionLeaseLost('remote_quiz_binding_changed')
            store = PostgresRemoteExecutionStore(SimpleNamespace(connection=lambda: nullcontext(conn)))
            store.complete(intent.owner_user_id, intent.execution_id, intent.lease_token, payload)
            return payload

