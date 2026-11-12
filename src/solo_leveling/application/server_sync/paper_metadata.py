"""논문 메타데이터 한 건을 outbox를 통해 중앙 서버에 동기화한다."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import sqlite3
from typing import Any
from uuid import UUID, uuid4

from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.infrastructure.storage.server_client import ServerConnectionRequired


@dataclass(frozen=True)
class SyncResult:
    operation_id: str
    status: str
    server_object_id: str | None = None
    error_code: str | None = None


class PaperMetadataSyncService:
    """동의, 재시도 키, payload 고정을 보장하는 독립 동기화 경계."""

    def __init__(self, db_path: str, client: Any, now=None):
        self._db_path = db_path
        self._client = client
        self._now = now or (lambda: datetime.now(timezone.utc))

    def grant_and_queue(self, paper_id: str, notice_version: str,
                        operation_id: str | None = None) -> SyncResult:
        """사용자의 명시적 논문 동기화 승인과 최초 전송 작업을 함께 기록한다."""
        if not notice_version.strip():
            raise ValueError("동의 안내문 버전이 필요합니다.")
        account_id = self._client.connection_id()
        consent_id = str(uuid4())
        operation_id = operation_id or str(uuid4())
        try:
            UUID(operation_id)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("동기화 작업 ID가 올바르지 않습니다.") from None
        now = _iso(self._now())
        conn = get_connection(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            source_id = _source_instance_id(conn)
            payload = _paper_payload(conn, paper_id, source_id)
            digest = _payload_hash(payload)
            conn.execute(
                """INSERT INTO server_sync_consents
                   (consent_scope_id, account_id, object_type, local_object_id,
                    notice_version, granted_at, revoked_at)
                   VALUES (?, ?, 'paper', ?, ?, ?, NULL)""",
                (consent_id, account_id, paper_id, notice_version.strip(), now),
            )
            conn.execute(
                """INSERT INTO sync_outbox
                   (operation_id, account_id, consent_scope_id, object_type, local_object_id,
                    operation, payload_hash, status, attempt_count, next_attempt_at,
                    last_error_code, created_at, updated_at)
                   VALUES (?, ?, ?, 'paper', ?, 'create', ?, 'pending', 0, NULL, NULL, ?, ?)""",
                (operation_id, account_id, consent_id, paper_id, digest, now, now),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return SyncResult(operation_id, "pending")

    def preview(self, paper_id: str) -> dict[str, str | None]:
        """전송을 시작하지 않고 사용자가 확인할 최소 논문 메타데이터만 반환한다."""
        self._client.connection_id()
        conn = get_connection(self._db_path)
        try:
            row = conn.execute("SELECT paper_id, title FROM papers WHERE paper_id=?", (paper_id,)).fetchone()
            if row is None:
                raise KeyError("논문을 찾을 수 없습니다.")
            return {"paper_id": row["paper_id"], "title": row["title"]}
        finally:
            conn.close()

    def status(self, operation_id: str) -> SyncResult:
        """현재 연결 계정에 속한 작업의 로컬 상태만 조회한다."""
        try:
            account_id = self._client.connection_id()
        except ServerConnectionRequired:
            return SyncResult(operation_id, "action_required", error_code="reauthentication_required")
        conn = get_connection(self._db_path)
        try:
            row = conn.execute(
                "SELECT * FROM sync_outbox WHERE operation_id=? AND account_id=?",
                (operation_id, account_id),
            ).fetchone()
            if row is None:
                raise KeyError("동기화 작업을 찾을 수 없습니다.")
            return _current_result(conn, row)
        finally:
            conn.close()

    def sync(self, operation_id: str) -> SyncResult:
        """한 작업을 전송한다. 실패해도 기존 로컬 논문 기능에는 예외를 전파하지 않는다."""
        try:
            account_id = self._client.connection_id()
        except ServerConnectionRequired:
            return SyncResult(operation_id, "action_required", error_code="reauthentication_required")
        conn = get_connection(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT o.*, c.revoked_at FROM sync_outbox o
                   JOIN server_sync_consents c USING (consent_scope_id)
                   WHERE o.operation_id = ? AND o.account_id = ?""",
                (operation_id, account_id),
            ).fetchone()
            if row is None:
                raise KeyError("동기화 작업을 찾을 수 없습니다.")
            if row["status"] in ("synced", "conflict"):
                return _current_result(conn, row)
            if row["revoked_at"] is not None:
                return _finish(conn, row, "action_required", "consent_revoked", self._now())
            source_id = _source_instance_id(conn)
            payload = _paper_payload(conn, row["local_object_id"], source_id)
            if _payload_hash(payload) != row["payload_hash"]:
                return _finish(conn, row, "conflict", "payload_changed", self._now())
            attempt = row["attempt_count"] + 1
            conn.execute(
                """UPDATE sync_outbox SET status='syncing', attempt_count=attempt_count+1,
                   last_error_code=NULL, updated_at=? WHERE operation_id=?""",
                (_iso(self._now()), operation_id),
            )
            conn.commit()
        finally:
            conn.close()

        try:
            response = self._client.request(
                "POST", "/v1/mcp/papers", json=payload, expected_connection_id=account_id,
                headers={"Idempotency-Key": operation_id},
            )
        except ServerConnectionRequired:
            return self._record_failure(operation_id, account_id, attempt, "action_required", "reauthentication_required")
        except Exception as exc:
            if not _is_transport_failure(exc):
                raise
            return self._record_failure(operation_id, account_id, attempt, "retryable_failure", "server_unavailable", retry=True)

        try:
            if response.status_code == 409:
                return self._record_failure(operation_id, account_id, attempt, "conflict", "server_conflict")
            if response.status_code == 422:
                return self._record_failure(operation_id, account_id, attempt, "action_required", "invalid_paper_request")
            if response.status_code >= 500:
                return self._record_failure(operation_id, account_id, attempt, "retryable_failure", "server_unavailable", retry=True)
            if response.status_code != 201:
                return self._record_failure(operation_id, account_id, attempt, "action_required", "unexpected_server_response")
            result = _validated_response(response.json())
        except (ValueError, TypeError, KeyError):
            return self._record_failure(operation_id, account_id, attempt, "action_required", "invalid_server_response")
        finally:
            response.close()

        conn = get_connection(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM sync_outbox WHERE operation_id=? AND account_id=?",
                (operation_id, account_id),
            ).fetchone()
            if row is None:
                raise KeyError("동기화 작업을 찾을 수 없습니다.")
            if row["attempt_count"] != attempt or row["status"] != "syncing":
                return _current_result(conn, row)
            now = _iso(self._now())
            conn.execute(
                """INSERT INTO server_object_mappings
                   (account_id, source_instance_id, object_type, local_object_id,
                    server_object_id, server_revision, synced_at)
                   VALUES (?, ?, 'paper', ?, ?, ?, ?)
                   ON CONFLICT(account_id, source_instance_id, object_type, local_object_id)
                   DO UPDATE SET server_object_id=excluded.server_object_id,
                                 server_revision=excluded.server_revision,
                                 synced_at=excluded.synced_at""",
                (account_id, source_id, row["local_object_id"], result["paper_id"], result["revision"], now),
            )
            conn.execute(
                """UPDATE sync_outbox SET status='synced', next_attempt_at=NULL,
                   last_error_code=NULL, updated_at=? WHERE operation_id=?""",
                (now, operation_id),
            )
            conn.commit()
            return SyncResult(operation_id, "synced", result["paper_id"])
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _record_failure(self, operation_id, account_id, attempt, status, code, retry=False):
        conn = get_connection(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM sync_outbox WHERE operation_id=? AND account_id=?",
                (operation_id, account_id),
            ).fetchone()
            if row is None:
                raise KeyError("동기화 작업을 찾을 수 없습니다.")
            if row["attempt_count"] != attempt or row["status"] != "syncing":
                return _current_result(conn, row)
            return _finish(conn, row, status, code, self._now(), retry)
        finally:
            conn.close()


def _source_instance_id(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT source_instance_id FROM server_sync_identity WHERE singleton=1").fetchone()
    if row:
        return row["source_instance_id"]
    value = str(uuid4())
    conn.execute("INSERT INTO server_sync_identity(singleton, source_instance_id) VALUES (1, ?)", (value,))
    return value


def _paper_payload(conn: sqlite3.Connection, paper_id: str, source_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT paper_id, title, created_at FROM papers WHERE paper_id=?", (paper_id,)).fetchone()
    if row is None:
        raise KeyError("논문을 찾을 수 없습니다.")
    return {
        "source_instance_id": source_id, "local_paper_id": row["paper_id"], "title": row["title"],
        "authors": None, "publication_year": None, "original_created_at": row["created_at"],
    }


def _payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _validated_response(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("sync_status") != "stored":
        raise ValueError
    if not isinstance(data.get("paper_id"), str) or not isinstance(data.get("server_updated_at"), str):
        raise ValueError
    UUID(data["paper_id"])
    if not isinstance(data.get("revision"), int) or isinstance(data["revision"], bool) or not 1 <= data["revision"] <= 2**63 - 1:
        raise ValueError
    value = datetime.fromisoformat(data["server_updated_at"].replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError
    return data


def _current_result(conn, row):
    if row["status"] != "synced":
        return SyncResult(row["operation_id"], row["status"], error_code=row["last_error_code"])
    mapping = conn.execute(
        """SELECT * FROM server_object_mappings
           WHERE account_id=? AND object_type=? AND local_object_id=?
             AND source_instance_id=(SELECT source_instance_id FROM server_sync_identity WHERE singleton=1)""",
        (row["account_id"], row["object_type"], row["local_object_id"]),
    ).fetchone()
    return SyncResult(row["operation_id"], "synced", mapping["server_object_id"])


def _finish(conn, row, status, code, now, retry=False):
    current = _iso(now)
    next_attempt = _iso(now + timedelta(minutes=min(60, 2 ** min(row["attempt_count"], 5)))) if retry else None
    conn.execute(
        """UPDATE sync_outbox SET status=?, next_attempt_at=?, last_error_code=?, updated_at=?
           WHERE operation_id=?""",
        (status, next_attempt, code, current, row["operation_id"]),
    )
    conn.commit()
    return SyncResult(row["operation_id"], status, error_code=code)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("시간대가 있는 시각이 필요합니다.")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_transport_failure(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    return exc.__class__.__module__.split(".")[0] in {"requests", "urllib3"}
