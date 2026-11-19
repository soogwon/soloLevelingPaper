"""요약 결과를 유효한 실행 세대에만 원자 저장하고 같은 요청으로 읽는다."""
from contextlib import nullcontext
from datetime import datetime
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID

from solo_leveling.application.summary.remote_contract import (
    OPERATION, NOTICE_VERSION, RemoteSummaryInput, validated_summary_payload, _load,
)
from .remote_executions import (
    PostgresRemoteExecutionStore, RemoteExecutionLeaseLost, validate_execution_scope, _question_consent,
)


class PostgresSummaryResults:
    def __init__(self, pool):
        self.pool = pool

    def input(self, intent):
        with self.pool.connection() as conn:
            if not validate_execution_scope(conn, intent):
                return None
            if _question_consent(conn, intent.owner_user_id, intent.storage_consent_id,
                                 intent.ai_consent_id, intent.input_hash, NOTICE_VERSION) is None:
                return None
            row = conn.execute("""SELECT s.input_snapshot FROM server_summary_inputs s
                JOIN server_remote_executions e ON e.owner_user_id=s.owner_user_id AND e.execution_id=s.execution_id
                WHERE s.owner_user_id=%s AND s.execution_id=%s AND e.operation=%s""",
                (intent.owner_user_id, intent.execution_id, OPERATION)).fetchone()
            if row is None:
                return None
            request = RemoteSummaryInput(json.dumps(row['input_snapshot']))
            if request.input_hash != intent.input_hash:
                raise ValueError('remote_summary_input_invalid')
            return request

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
                  AND p.deleted_at IS NULL FOR SHARE OF p''', (owner, context, OPERATION, key)).fetchone()
        if row is None:
            return None
        payload = None
        if row['state'] == 'completed':
            try:
                raw = row['payload_json']
                if hashlib.sha256(raw.encode()).hexdigest() != row['payload_sha256']:
                    raise ValueError()
                payload = _load(raw)
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

    def evidence(self,owner,context,request_key,evidence_id):
        # 같은 조회 transaction의 고정 결과·입력만 읽고 별도 검색이나 생성은 하지 않는다.
        with self.pool.connection() as conn:
            scoped = PostgresSummaryResults(SimpleNamespace(connection=lambda:nullcontext(conn)))
            result = scoped.get(owner,context,request_key)
            if result is None or result['result'] is None:
                return None
            used = {eid for item in result['result']['items'] for eid in item['evidence_ids']}
            if evidence_id not in used:
                return None
            row = conn.execute('''SELECT input_snapshot FROM server_summary_inputs
                WHERE owner_user_id=%s AND execution_id=%s''',(owner,result['execution_id'])).fetchone()
            request = RemoteSummaryInput(json.dumps(row['input_snapshot']))
            binding,sources,_ = request.unpack()
            source = next((s for s in sources if s.evidence_id==evidence_id),None)
            if source is None:
                raise ValueError('remote_summary_evidence_invalid')
            return dict(context_id=context,request_key=request_key,evidence_id=evidence_id,
                        paper_id=binding.paper_id,version_id=binding.server_version_id,
                        publication_id=binding.publication_id,parse_revision_id=binding.parse_revision_id,
                        translation_revision_id=binding.translation_revision_id,chunk_id=source.chunk_id,
                        pdf_page=source.pdf_page,section_id=source.section_id,
                        original_text=source.original_text,translated_text=source.translated_text)

    def latest(self,owner,context):
        with self.pool.connection() as conn:
            row = conn.execute('''SELECT request_key FROM server_remote_executions
                WHERE owner_user_id=%s AND context_id=%s AND operation=%s AND state='completed'
                ORDER BY finished_at DESC,execution_id DESC LIMIT 1''',(owner,str(UUID(context)),OPERATION)).fetchone()
            if row is None:
                return None
            return PostgresSummaryResults(SimpleNamespace(connection=lambda:nullcontext(conn))).get(owner,context,str(row['request_key']))

    def contexts(self, owner, paper, version, after=None):
        paper = str(UUID(paper))
        after = str(UUID(after)) if after is not None else None
        if not isinstance(version, str) or not version.strip() or len(version) > 128:
            raise ValueError('summary_version_invalid')
        with self.pool.connection() as conn:
            rows = conn.execute("""SELECT c.context_id,c.paper_id,c.server_version_id,c.publication_id
                FROM server_execution_contexts c JOIN server_papers p
                  ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                WHERE c.owner_user_id=%s AND c.paper_id=%s AND c.server_version_id=%s
                  AND p.deleted_at IS NULL AND (%s::uuid IS NULL OR c.context_id>%s::uuid)
                  AND EXISTS(SELECT 1 FROM server_remote_executions e JOIN server_summary_inputs s
                    ON s.owner_user_id=e.owner_user_id AND s.execution_id=e.execution_id
                    WHERE e.owner_user_id=c.owner_user_id AND e.context_id=c.context_id
                      AND e.operation=%s AND e.state='completed')
                ORDER BY c.context_id LIMIT 21""",(owner,paper,version,after,after,OPERATION)).fetchall()
        items = [{key:str(row[key]) for key in ('context_id','paper_id','server_version_id','publication_id')} for row in rows[:20]]
        return dict(items=items,next_cursor=items[-1]['context_id'] if len(rows)>20 else None)
