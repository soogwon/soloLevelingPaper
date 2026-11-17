"""원격 질문 실행의 멱등 접수, lease, 결과 저장과 읽기 복구."""

import hashlib
import json
from uuid import uuid4

from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.application.provider_dispatch import DispatchIntent
from solo_leveling.application.remote_replay import ExecutionLookup, StoredExecution


# 실행 상태와 별개로 원장의 미확정 사용량을 같은 조회 snapshot에 포함한다.
_USAGE_UNKNOWN = """EXISTS (
    SELECT 1 FROM server_provider_attempts a
    WHERE a.owner_user_id=e.owner_user_id AND a.execution_id=e.execution_id
      AND (a.status='unknown' OR (a.status='reserved' AND (
          e.lease_token IS DISTINCT FROM a.lease_token OR
          e.lease_expires_at<=CURRENT_TIMESTAMP)))
) AS usage_unknown"""


class RemoteExecutionConflict(ValueError):
    pass


class RemoteExecutionLeaseLost(ValueError):
    pass


class PostgresRemoteExecutionStore:
    def __init__(self, pool, admit=None):
        self._pool = pool
        self._admit = admit

    def create(
        self,
        lookup: ExecutionLookup,
        storage_consent_id: str,
        ai_consent_id: str,
        notice_version: str,
        *, policy: RemoteExecutionPolicy | None = None,
        question_input=None,
    ) -> StoredExecution | None:
        if not isinstance(lookup, ExecutionLookup) or not _strings(storage_consent_id, ai_consent_id, notice_version):
            raise ValueError("remote_execution_input_invalid")
        if policy is not None and not isinstance(policy, RemoteExecutionPolicy):
            raise ValueError("remote_execution_policy_invalid")
        if question_input is not None:
            from solo_leveling.application.evidence_qa.remote_question_input import RemoteQuestionInput
            if not isinstance(question_input, RemoteQuestionInput) or policy is None:
                raise ValueError('remote_question_input_invalid')
            binding, _, _, _, config = question_input.unpack()
            if (lookup.operation != 'ask_paper' or lookup.owner_user_id != binding.owner_user_id
                    or lookup.context_id != binding.context_id or lookup.input_hash != question_input.input_hash
                    or policy.version != config['budget_policy_version']):
                raise ValueError('remote_question_input_invalid')
        execution_id = str(uuid4())
        with self._pool.connection() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"{lookup.owner_user_id}\x1f{lookup.operation}\x1f{lookup.request_key}",),
            )
            old = conn.execute(
                f"""SELECT e.*,p.deleted_at,{_USAGE_UNKNOWN} FROM server_remote_executions e
                   JOIN server_execution_contexts c
                     ON c.owner_user_id=e.owner_user_id AND c.context_id=e.context_id
                   JOIN server_papers p
                     ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                   WHERE e.owner_user_id=%s AND e.operation=%s AND e.request_key=%s
                   FOR UPDATE OF e,p""",
                (lookup.owner_user_id, lookup.operation, lookup.request_key),
            ).fetchone()
            if old is not None:
                _same_input(old, lookup, storage_consent_id, ai_consent_id, notice_version)
                if question_input is not None and old['question_snapshot'] is None:
                    raise RemoteExecutionConflict('remote_execution_input_conflict')
                return _stored(old)
            if question_input is not None:
                from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
                # 같은 연결에서 고정 publication을 대조하며 입력 본문은 진단에 남기지 않는다.
                from contextlib import nullcontext
                from types import SimpleNamespace
                current = PostgresRemoteContextResolver(SimpleNamespace(connection=lambda: nullcontext(conn))).resolve(
                    lookup.owner_user_id, lookup.context_id)
                if current != binding:
                    return None
                consent = _question_consent(conn,lookup.owner_user_id,storage_consent_id,ai_consent_id,lookup.input_hash,notice_version)
                if consent is None:
                    return None
            if self._admit is not None and not self._admit(
                conn, lookup, storage_consent_id, ai_consent_id, notice_version
            ):
                return None
            row = conn.execute(
                """INSERT INTO server_remote_executions
                       (execution_id,owner_user_id,operation,request_key,input_hash,context_id,
                        storage_consent_id,ai_consent_id,notice_version,state,budget_state,policy_snapshot,deadline_at,question_snapshot)
                   SELECT %s,c.owner_user_id,%s,%s,%s,c.context_id,%s,%s,%s,'queued','available',%s::jsonb,
                          CASE WHEN %s::integer IS NULL THEN NULL ELSE CURRENT_TIMESTAMP+make_interval(secs=>%s) END,%s::jsonb
                   FROM server_execution_contexts c
                   JOIN server_papers p
                     ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                   WHERE c.owner_user_id=%s AND c.context_id=%s AND p.deleted_at IS NULL
                   ON CONFLICT (owner_user_id,operation,request_key) DO NOTHING
                   RETURNING *,NULL::timestamptz AS deleted_at""",
                (
                    execution_id,
                    lookup.operation,
                    lookup.request_key,
                    lookup.input_hash,
                    storage_consent_id,
                    ai_consent_id,
                    notice_version,
                    json.dumps(policy.snapshot()) if policy else None,
                    policy.timeout_seconds if policy else None,
                    policy.timeout_seconds if policy else None,
                    question_input.encoded if question_input else None,
                    lookup.owner_user_id,
                    lookup.context_id,
                ),
            ).fetchone()
            if row is None:
                # 동시 접수와 미존재/삭제 context를 구분하되 외부에는 둘 다 None을 허용한다.
                old = conn.execute(
                    f"""SELECT e.*,p.deleted_at,{_USAGE_UNKNOWN} FROM server_remote_executions e
                       JOIN server_execution_contexts c
                         ON c.owner_user_id=e.owner_user_id AND c.context_id=e.context_id
                       JOIN server_papers p
                         ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                       WHERE e.owner_user_id=%s AND e.operation=%s AND e.request_key=%s
                       FOR UPDATE OF e,p""",
                    (lookup.owner_user_id, lookup.operation, lookup.request_key),
                ).fetchone()
                if old is None:
                    return None
                _same_input(old, lookup, storage_consent_id, ai_consent_id, notice_version)
                return _stored(old)
        return _stored(row)

    def claim(self, owner_user_id: str, execution_id: str, *, lease_seconds: int = 60) -> DispatchIntent | None:
        if not _strings(owner_user_id, execution_id) or type(lease_seconds) is not int or lease_seconds < 1:
            raise ValueError("remote_execution_input_invalid")
        lease_token = str(uuid4())
        with self._pool.connection() as conn:
            row = conn.execute(
                """UPDATE server_remote_executions e
                   SET state='running',lease_token=%s,
                       lease_expires_at=CURRENT_TIMESTAMP+make_interval(secs=>%s),
                       lease_generation=lease_generation+1,
                       updated_at=CURRENT_TIMESTAMP
                   FROM server_execution_contexts c,server_papers p
                   WHERE e.owner_user_id=%s AND e.execution_id=%s
                     AND (e.state='queued' OR (e.state='running' AND e.lease_expires_at<=CURRENT_TIMESTAMP))
                     AND (e.deadline_at IS NULL OR e.deadline_at>clock_timestamp())
                     AND c.owner_user_id=e.owner_user_id AND c.context_id=e.context_id
                     AND p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                     AND p.deleted_at IS NULL
                   RETURNING e.*""",
                (lease_token, lease_seconds, owner_user_id, execution_id),
            ).fetchone()
        return None if row is None else _intent(row)

    def claim_next_question(self):
        """행 잠금으로 단일 질문을 선택한다. 이전 전송 이력은 자동 재호출하지 않는다."""
        from contextlib import nullcontext
        from types import SimpleNamespace
        with self._pool.connection() as conn:
            row = conn.execute("""SELECT e.* FROM server_remote_executions e
                WHERE e.operation='ask_paper' AND e.question_snapshot IS NOT NULL
                  AND e.policy_snapshot IS NOT NULL
                  AND (e.state='queued' OR (e.state='running' AND e.lease_expires_at<=clock_timestamp()))
                ORDER BY e.created_at,e.execution_id FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
            if row is None:
                return None
            owner, execution = row['owner_user_id'], str(row['execution_id'])
            scoped = PostgresRemoteExecutionStore(SimpleNamespace(connection=lambda: nullcontext(conn)))
            if scoped.expire(owner, execution):
                return ('expired', None, None)
            paper = conn.execute("""SELECT 1 FROM server_execution_contexts c JOIN server_papers p
                ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                WHERE c.owner_user_id=%s AND c.context_id=%s AND p.deleted_at IS NULL
                FOR UPDATE OF p""", (owner,row['context_id'])).fetchone()
            consent = _question_consent(conn,owner,row['storage_consent_id'],row['ai_consent_id'],row['input_hash'],row['notice_version'])
            attempted = conn.execute("SELECT 1 FROM server_provider_attempts WHERE owner_user_id=%s AND execution_id=%s LIMIT 1",(owner,execution)).fetchone()
            if paper is None or consent is None or attempted is not None:
                reason = 'provider_failed' if attempted is not None else 'scope_revoked'
                conn.execute("""UPDATE server_remote_executions SET state='failed',terminal_reason=%s,
                    lease_token=NULL,lease_expires_at=NULL,finished_at=clock_timestamp(),updated_at=clock_timestamp()
                    WHERE owner_user_id=%s AND execution_id=%s""",(reason,owner,execution))
                return ('blocked', None, None)
            policy = RemoteExecutionPolicy(**row['policy_snapshot'])
            intent = scoped.claim(owner,execution,lease_seconds=min(policy.timeout_seconds,max(3,policy.stage_timeout_seconds)))
            return ('claimed',intent,policy) if intent else ('blocked',None,None)

    def question_input(self, intent):
        """현재 lease·동의·접근 범위를 확인한 worker만 저장 입력을 읽는다."""
        from solo_leveling.application.evidence_qa.remote_question_input import RemoteQuestionInput
        with self._pool.connection() as conn:
            if not validate_execution_scope(conn, intent):
                return None
            if _question_consent(conn,intent.owner_user_id,intent.storage_consent_id,intent.ai_consent_id,intent.input_hash,intent.notice_version) is None:
                return None
            row = conn.execute("SELECT question_snapshot,context_id,policy_snapshot FROM server_remote_executions WHERE owner_user_id=%s AND execution_id=%s AND lease_token=%s",
                               (intent.owner_user_id,intent.execution_id,intent.lease_token)).fetchone()
            if row is None or row['question_snapshot'] is None:
                return None
            request = RemoteQuestionInput(json.dumps(row['question_snapshot']))
            binding, _, _, _, config = request.unpack()
            if (request.input_hash != intent.input_hash or binding.owner_user_id != intent.owner_user_id
                    or binding.context_id != str(row['context_id']) or row['policy_snapshot'] is None
                    or config['budget_policy_version'] != row['policy_snapshot']['version']):
                raise ValueError('remote_question_input_invalid')
            return request

    def policy(self, owner_user_id, execution_id):
        with self._pool.connection() as conn:
            row = conn.execute("SELECT policy_snapshot FROM server_remote_executions WHERE owner_user_id=%s AND execution_id=%s",
                               (owner_user_id, execution_id)).fetchone()
        return RemoteExecutionPolicy(**row['policy_snapshot']) if row and row['policy_snapshot'] else None

    def heartbeat(self, intent, *, lease_seconds=60):
        if type(lease_seconds) is not int or lease_seconds < 1:
            raise ValueError('remote_execution_input_invalid')
        with self._pool.connection() as conn:
            if not validate_execution_scope(conn, intent):
                return False
            row = conn.execute("""UPDATE server_remote_executions SET
                  lease_expires_at=LEAST(deadline_at,clock_timestamp()+make_interval(secs=>%s)),
                  updated_at=clock_timestamp()
                WHERE owner_user_id=%s AND execution_id=%s AND lease_token=%s
                  AND policy_snapshot IS NOT NULL AND deadline_at>clock_timestamp()
                RETURNING execution_id""", (lease_seconds,intent.owner_user_id,intent.execution_id,intent.lease_token)).fetchone()
        return row is not None

    def fail(self, intent, reason):
        if reason not in ('budget_exhausted','deadline_exceeded','provider_failed','scope_revoked','internal_failed'):
            raise ValueError('remote_execution_reason_invalid')
        with self._pool.connection() as conn:
            row = conn.execute("""UPDATE server_remote_executions SET state='failed',
                  terminal_reason=CASE WHEN deadline_at<=clock_timestamp() THEN 'deadline_exceeded' ELSE %s END,
                  budget_state=CASE WHEN %s='budget_exhausted' THEN 'limited' ELSE budget_state END,
                  lease_token=NULL,lease_expires_at=NULL,finished_at=clock_timestamp(),updated_at=clock_timestamp()
                WHERE owner_user_id=%s AND execution_id=%s AND state='running' AND lease_token=%s
                RETURNING execution_id""",(reason,reason,intent.owner_user_id,intent.execution_id,intent.lease_token)).fetchone()
        return row is not None

    def expire(self, owner_user_id, execution_id):
        with self._pool.connection() as conn:
            row = conn.execute("""UPDATE server_remote_executions SET state='failed',terminal_reason='deadline_exceeded',
                  lease_token=NULL,lease_expires_at=NULL,finished_at=clock_timestamp(),updated_at=clock_timestamp()
                WHERE owner_user_id=%s AND execution_id=%s AND state IN ('queued','running')
                  AND deadline_at<=clock_timestamp() RETURNING execution_id""",(owner_user_id,execution_id)).fetchone()
        return row is not None

    def lookup(self, owner_user_id: str, operation: str, request_key: str) -> StoredExecution | None:
        if not _strings(owner_user_id, operation, request_key):
            raise ValueError("remote_execution_input_invalid")
        with self._pool.connection() as conn:
            row = conn.execute(
                f"""SELECT e.*,p.deleted_at,{_USAGE_UNKNOWN} FROM server_remote_executions e
                   JOIN server_execution_contexts c
                     ON c.owner_user_id=e.owner_user_id AND c.context_id=e.context_id
                   JOIN server_papers p
                     ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                   WHERE e.owner_user_id=%s AND e.operation=%s AND e.request_key=%s""",
                (owner_user_id, operation, request_key),
            ).fetchone()
        return None if row is None else _stored(row)

    def complete(self, owner_user_id: str, execution_id: str, lease_token: str, payload: dict) -> None:
        if (
            not _strings(owner_user_id, execution_id, lease_token)
            or not isinstance(payload, dict)
            or not _strings(payload.get("context_id"))
        ):
            raise ValueError("remote_execution_result_invalid")
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with self._pool.connection() as conn:
            row = conn.execute(
                """UPDATE server_remote_executions e
                   SET state='completed',payload_json=%s,payload_sha256=%s,
                       lease_token=NULL,lease_expires_at=NULL,
                       finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                   FROM server_execution_contexts c,server_papers p
                   WHERE e.owner_user_id=%s AND e.execution_id=%s AND e.state='running'
                     AND e.lease_token=%s AND e.lease_expires_at>clock_timestamp()
                     AND (e.deadline_at IS NULL OR e.deadline_at>clock_timestamp())
                     AND e.context_id=%s
                     AND c.owner_user_id=e.owner_user_id AND c.context_id=e.context_id
                     AND p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                     AND p.deleted_at IS NULL
                   RETURNING e.execution_id""",
                (raw, digest, owner_user_id, execution_id, lease_token, payload["context_id"]),
            ).fetchone()
        if row is None:
            raise RemoteExecutionLeaseLost("remote_execution_lease_lost")

    def request_cancel(self, owner_user_id: str, execution_id: str) -> str | None:
        if not _strings(owner_user_id, execution_id):
            raise ValueError("remote_execution_input_invalid")
        with self._pool.connection() as conn:
            row = conn.execute(
                """UPDATE server_remote_executions
                   SET state='cancel_requested',lease_token=NULL,lease_expires_at=NULL,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE owner_user_id=%s AND execution_id=%s AND state IN ('queued','running')
                   RETURNING state""",
                (owner_user_id, execution_id),
            ).fetchone()
        return None if row is None else row["state"]

    def confirm_cancel(self, owner_user_id: str, execution_id: str) -> bool:
        if not _strings(owner_user_id, execution_id):
            raise ValueError("remote_execution_input_invalid")
        with self._pool.connection() as conn:
            row = conn.execute(
                """UPDATE server_remote_executions
                   SET state='cancelled',terminal_reason='cancelled',finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                   WHERE owner_user_id=%s AND execution_id=%s AND state='cancel_requested'
                   RETURNING execution_id""",
                (owner_user_id, execution_id),
            ).fetchone()
        return row is not None


def validate_execution_scope(conn, intent: DispatchIntent) -> bool:
    """#396 원장이 같은 transaction에서 호출하는 실행 세대/tombstone 잠금."""
    if not isinstance(intent, DispatchIntent):
        return False
    row = conn.execute(
        """SELECT 1 FROM server_remote_executions e
           JOIN server_execution_contexts c
             ON c.owner_user_id=e.owner_user_id AND c.context_id=e.context_id
           JOIN server_papers p
             ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
           WHERE e.owner_user_id=%s AND e.execution_id=%s AND e.state='running'
             AND e.lease_token=%s AND e.lease_expires_at>clock_timestamp()
                     AND (e.deadline_at IS NULL OR e.deadline_at>clock_timestamp()) AND e.input_hash=%s
             AND e.storage_consent_id=%s AND e.ai_consent_id=%s AND e.notice_version=%s
             AND p.deleted_at IS NULL
           FOR UPDATE OF e,p""",
        (
            intent.owner_user_id,
            intent.execution_id,
            intent.lease_token,
            intent.input_hash,
            intent.storage_consent_id,
            intent.ai_consent_id,
            intent.notice_version,
        ),
    ).fetchone()
    return row is not None


def _same_input(row, lookup, storage_consent_id, ai_consent_id, notice_version):
    actual = (
        row["input_hash"],
        str(row["context_id"]),
        row["storage_consent_id"],
        row["ai_consent_id"],
        row["notice_version"],
    )
    expected = (lookup.input_hash, lookup.context_id, storage_consent_id, ai_consent_id, notice_version)
    if actual != expected:
        raise RemoteExecutionConflict("remote_execution_request_conflict")


def _stored(row):
    lookup = ExecutionLookup(
        row["owner_user_id"], row["operation"], row["request_key"], row["input_hash"], str(row["context_id"])
    )
    available = row.get("payload_json") is not None
    return StoredExecution(
        lookup,
        row["execution_id"],
        row["state"],
        "usage_unknown" if row.get("usage_unknown") and not row.get("policy_snapshot") else row["budget_state"],
        row.get("deleted_at") is None,
        available,
        row.get("payload_json"),
        row.get("payload_sha256"),
        row.get("terminal_reason"),
        "unknown" if row.get("usage_unknown") else "known",
    )


def _intent(row):
    return DispatchIntent(
        row["owner_user_id"],
        row["execution_id"],
        row["lease_token"],
        row["input_hash"],
        row["storage_consent_id"],
        row["ai_consent_id"],
        row["notice_version"],
    )


def _strings(*values):
    return all(isinstance(value, str) and value.strip() for value in values)


def _question_consent(conn,owner,storage_id,ai_id,input_hash,notice):
    return conn.execute("""SELECT 1 FROM server_dispatch_controls control
                    JOIN server_remote_consents storage ON storage.owner_user_id=%s
                      AND storage.consent_id=%s AND storage.purpose='storage'
                      AND storage.input_hash=%s AND storage.notice_version=%s AND storage.revoked_at IS NULL
                    JOIN server_remote_consents ai ON ai.owner_user_id=storage.owner_user_id
                      AND ai.consent_id=%s AND ai.purpose='ai_provider'
                      AND ai.input_hash=storage.input_hash AND ai.notice_version=storage.notice_version AND ai.revoked_at IS NULL
                    WHERE control.control_id='provider_dispatch' AND control.dispatch_enabled
                    FOR SHARE OF control,storage,ai""",
                    (owner,storage_id,input_hash,notice,ai_id)).fetchone()
