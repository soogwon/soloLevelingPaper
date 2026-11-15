"""원격 질문 실행의 멱등 접수, lease, 결과 저장과 읽기 복구."""

import hashlib
import json
from uuid import uuid4

from solo_leveling.application.provider_dispatch import DispatchIntent
from solo_leveling.application.remote_replay import ExecutionLookup, StoredExecution


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
    ) -> StoredExecution | None:
        if not isinstance(lookup, ExecutionLookup) or not _strings(storage_consent_id, ai_consent_id, notice_version):
            raise ValueError("remote_execution_input_invalid")
        execution_id = str(uuid4())
        with self._pool.connection() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"{lookup.owner_user_id}\x1f{lookup.operation}\x1f{lookup.request_key}",),
            )
            old = conn.execute(
                """SELECT e.*,p.deleted_at FROM server_remote_executions e
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
                return _stored(old)
            if self._admit is not None and not self._admit(
                conn, lookup, storage_consent_id, ai_consent_id, notice_version
            ):
                return None
            row = conn.execute(
                """INSERT INTO server_remote_executions
                       (execution_id,owner_user_id,operation,request_key,input_hash,context_id,
                        storage_consent_id,ai_consent_id,notice_version,state,budget_state)
                   SELECT %s,c.owner_user_id,%s,%s,%s,c.context_id,%s,%s,%s,'queued','available'
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
                    lookup.owner_user_id,
                    lookup.context_id,
                ),
            ).fetchone()
            if row is None:
                # 동시 접수와 미존재/삭제 context를 구분하되 외부에는 둘 다 None을 허용한다.
                old = conn.execute(
                    """SELECT e.*,p.deleted_at FROM server_remote_executions e
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
                     AND c.owner_user_id=e.owner_user_id AND c.context_id=e.context_id
                     AND p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                     AND p.deleted_at IS NULL
                   RETURNING e.*""",
                (lease_token, lease_seconds, owner_user_id, execution_id),
            ).fetchone()
        return None if row is None else _intent(row)

    def lookup(self, owner_user_id: str, operation: str, request_key: str) -> StoredExecution | None:
        if not _strings(owner_user_id, operation, request_key):
            raise ValueError("remote_execution_input_invalid")
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT e.*,p.deleted_at FROM server_remote_executions e
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
                     AND e.lease_token=%s AND e.lease_expires_at>CURRENT_TIMESTAMP
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
                   SET state='cancelled',finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
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
             AND e.lease_token=%s AND e.lease_expires_at>CURRENT_TIMESTAMP AND e.input_hash=%s
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
        row["budget_state"],
        row.get("deleted_at") is None,
        available,
        row.get("payload_json"),
        row.get("payload_sha256"),
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
