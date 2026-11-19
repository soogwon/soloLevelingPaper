"""요약 결과를 유효한 실행 세대에만 원자 저장하고 같은 요청으로 읽는다."""
from contextlib import nullcontext
from datetime import datetime
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID

from solo_leveling.application.summary.remote_contract import (
    OPERATION, NOTICE_VERSION, RemoteSummaryInput, validated_summary_payload,
)
from .remote_executions import (
    PostgresRemoteExecutionStore, RemoteExecutionLeaseLost, validate_execution_scope, _question_consent,
)


class PostgresSummaryResults:
    def __init__(self, pool):
        self.pool = pool

    def complete(self, intent, items):
        with self.pool.connection() as conn:
            if not validate_execution_scope(conn, intent):
                raise RemoteExecutionLeaseLost('remote_summary_lease_lost')
            if _question_consent(conn, intent.owner_user_id, intent.storage_consent_id,
                                 intent.ai_consent_id, intent.input_hash, NOTICE_VERSION) is None:
                raise RemoteExecutionLeaseLost('remote_summary_consent_revoked')
            row = conn.execute('''SELECT s.input_snapshot,clock_timestamp() AS generated_at
                FROM server_summary_inputs s JOIN server_remote_executions e
                ON e.owner_user_id=s.owner_user_id AND e.execution_id=s.execution_id
                WHERE s.owner_user_id=%s AND s.execution_id=%s AND e.operation=%s''',
                (intent.owner_user_id, intent.execution_id, OPERATION)).fetchone()
            if row is None:
                raise ValueError('remote_summary_execution_invalid')
            request = RemoteSummaryInput(json.dumps(row['input_snapshot']))
            if request.input_hash != intent.input_hash:
                raise ValueError('remote_summary_input_invalid')
            attempts = conn.execute('''SELECT status,stage,lease_token FROM server_provider_attempts
                WHERE owner_user_id=%s AND execution_id=%s FOR SHARE''',
                (intent.owner_user_id, intent.execution_id)).fetchall()
            if (len(attempts) != 1 or attempts[0]['status'] != 'observed'
                    or attempts[0]['stage'] != 'generation' or attempts[0]['lease_token'] != intent.lease_token):
                raise ValueError('remote_summary_usage_unconfirmed')
            payload = validated_summary_payload(request, items, generated_at=row['generated_at'])
            payload['context_id'] = request.unpack()[0].context_id
            store = PostgresRemoteExecutionStore(SimpleNamespace(connection=lambda: nullcontext(conn)))
            store.complete(intent.owner_user_id, intent.execution_id, intent.lease_token, payload)
            return payload

    def get(self, owner, context, request_key):
        context, key = str(UUID(context)), str(UUID(request_key))
        with self.pool.connection() as conn:
            row = conn.execute('''SELECT e.execution_id,e.context_id,e.state,e.payload_json,e.payload_sha256,
                s.input_snapshot FROM server_remote_executions e
                JOIN server_summary_inputs s ON s.owner_user_id=e.owner_user_id AND s.execution_id=e.execution_id
                JOIN server_execution_contexts c ON c.owner_user_id=e.owner_user_id AND c.context_id=e.context_id
                JOIN server_papers p ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                WHERE e.owner_user_id=%s AND e.context_id=%s AND e.operation=%s AND e.request_key=%s
                  AND p.deleted_at IS NULL''', (owner, context, OPERATION, key)).fetchone()
        if row is None:
            return None
        payload = None
        if row['state'] == 'completed':
            try:
                raw = row['payload_json']
                if hashlib.sha256(raw.encode()).hexdigest() != row['payload_sha256']:
                    raise ValueError()
                payload = json.loads(raw)
                request = RemoteSummaryInput(json.dumps(row['input_snapshot']))
                if request.unpack()[0].context_id != context or request.unpack()[0].owner_user_id != owner:
                    raise ValueError()
                expected = validated_summary_payload(request, payload['items'],
                    generated_at=datetime.fromisoformat(payload['generated_at']))
                expected['context_id'] = context
                if payload != expected:
                    raise ValueError()
            except (TypeError, ValueError, KeyError, AttributeError):
                raise ValueError('remote_summary_result_invalid') from None
        return dict(execution_id=row['execution_id'], context_id=context, request_key=key,
                    state=row['state'], result=payload)
