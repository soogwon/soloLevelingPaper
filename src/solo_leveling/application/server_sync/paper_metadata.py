"""논문 메타데이터 한 건을 outbox를 통해 중앙 서버에 동기화한다."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import sqlite3
from threading import RLock

from filelock import FileLock, Timeout
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.infrastructure.storage.server_client import ServerConnectionRequired


SERVER_SYNC_NOTICE_VERSION = "paper-learning-summary-annotation-sync-v4"


class SyncPreviewChanged(ValueError):
    """카드에서 확인한 논문 또는 연결 계정이 달라졌다."""


@dataclass(frozen=True)
class PaperSyncPreview:
    account_id: str
    paper_id: str
    title: str | None
    created_at: str
    learning_record_count: int = 0
    last_studied_at: str | None = None
    learning_hash: str | None = None
    summary_count: int = 0
    summary_hash: str | None = None
    annotation_count: int = 0
    annotation_hash: str | None = None


@dataclass(frozen=True)
class SyncResult:
    operation_id: str
    status: str
    server_object_id: str | None = None
    error_code: str | None = None


class PaperMetadataSyncService:
    """동의, 재시도 키, payload 고정을 보장하는 독립 동기화 경계."""

    def __init__(self, db_path: str, client: Any, now=None):
        self._sync_lock = RLock()
        self._process_lock = FileLock(str(Path(db_path).resolve()) + ".sync.lock")
        self._db_path = db_path
        self._client = client
        self._now = now or (lambda: datetime.now(timezone.utc))

    def grant_and_queue(self, paper_id: str, notice_version: str,
                        operation_id: str | None = None, preview: PaperSyncPreview | None = None) -> SyncResult:
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
            learning_payloads = _learning_payloads(conn, paper_id, source_id)
            summary_payloads = (_summary_payloads(conn, paper_id, source_id)
                                if notice_version.strip() == SERVER_SYNC_NOTICE_VERSION else [])
            annotation_payloads = (_annotation_payloads(conn, paper_id, source_id)
                                   if notice_version.strip() == SERVER_SYNC_NOTICE_VERSION else [])
            if preview is not None and preview != PaperSyncPreview(
                account_id, payload["local_paper_id"], payload["title"], payload["original_created_at"],
                len(learning_payloads), _latest_timestamp(
                    item["last_studied_at"] for item in learning_payloads),
                _learning_hash(learning_payloads),
                len(summary_payloads), _summary_hash(summary_payloads),
                len(annotation_payloads), _annotation_hash(annotation_payloads),
            ):
                raise SyncPreviewChanged
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
            for learning in learning_payloads:
                conn.execute(
                    """INSERT INTO sync_outbox
                       (operation_id, account_id, consent_scope_id, object_type, local_object_id,
                        local_subobject_id, operation, payload_hash, status, attempt_count,
                        next_attempt_at, last_error_code, created_at, updated_at)
                       VALUES (?, ?, ?, 'learning_record', ?, ?, 'upsert', ?, 'pending', 0,
                               NULL, NULL, ?, ?)""",
                    (str(uuid4()), account_id, consent_id, paper_id,
                     learning["local_learning_record_id"], _payload_hash(learning), now, now),
                )
            for summary in summary_payloads:
                conn.execute(
                    """INSERT INTO sync_outbox
                       (operation_id, account_id, consent_scope_id, object_type, local_object_id,
                        local_subobject_id, operation, payload_hash, status, attempt_count,
                        next_attempt_at, last_error_code, created_at, updated_at)
                       VALUES (?, ?, ?, 'summary', ?, ?, 'upsert', ?, 'pending', 0,
                               NULL, NULL, ?, ?)""",
                    (str(uuid4()), account_id, consent_id, paper_id,
                     summary["local_summary_id"], _payload_hash(summary), now, now),
                )
            for annotation in annotation_payloads:
                conn.execute(
                    """INSERT INTO sync_outbox
                       (operation_id, account_id, consent_scope_id, object_type, local_object_id,
                        local_subobject_id, operation, payload_hash, status, attempt_count,
                        next_attempt_at, last_error_code, created_at, updated_at)
                       VALUES (?, ?, ?, 'annotation', ?, ?, 'upsert', ?, 'pending', 0,
                               NULL, NULL, ?, ?)""",
                    (str(uuid4()), account_id, consent_id, paper_id,
                     annotation["local_annotation_id"], _payload_hash(annotation), now, now),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return SyncResult(operation_id, "pending")

    def preview(self, paper_id: str) -> PaperSyncPreview:
        """전송을 시작하지 않고 사용자가 확인할 최소 논문 메타데이터만 반환한다."""
        account_id = self._client.connection_id()
        conn = get_connection(self._db_path)
        try:
            conn.execute("BEGIN")
            row = _paper_row(conn, paper_id)
            if row is None:
                raise KeyError("논문을 찾을 수 없습니다.")
            records = _learning_payloads(conn, paper_id, "")
            summaries = _summary_payloads(conn, paper_id, "")
            annotations = _annotation_payloads(conn, paper_id, "")
            return PaperSyncPreview(
                account_id, row["paper_id"], _effective_title(row), row["created_at"],
                len(records), _latest_timestamp(item["last_studied_at"] for item in records),
                _learning_hash(records),
                len(summaries), _summary_hash(summaries),
                len(annotations), _annotation_hash(annotations),
            )
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
                "SELECT * FROM sync_outbox WHERE operation_id=? AND account_id=? AND object_type='paper'",
                (operation_id, account_id),
            ).fetchone()
            if row is None:
                raise KeyError("동기화 작업을 찾을 수 없습니다.")
            result = _current_result(conn, row)
            if result.status == "synced":
                child = conn.execute(
                    """SELECT status, last_error_code FROM sync_outbox WHERE consent_scope_id=?
                       AND object_type IN ('learning_record', 'summary', 'annotation') AND status!='synced'
                       ORDER BY CASE status WHEN 'conflict' THEN 0 WHEN 'action_required' THEN 1
                           WHEN 'retryable_failure' THEN 2 ELSE 3 END, operation_id LIMIT 1""",
                    (row["consent_scope_id"],),
                ).fetchone()
                if child:
                    return SyncResult(operation_id, child["status"], result.server_object_id, child["last_error_code"])
            return result
        finally:
            conn.close()

    def retry_due(self, limit: int = 10) -> list[SyncResult]:
        """기존 승인 작업만 재시도 시각·상한에 맞춰 재개한다. 새 작업은 만들지 않는다."""
        with self._sync_lock:
            try:
                self._process_lock.acquire(timeout=0)
            except Timeout:
                return []
            try:
                return self._retry_due(limit)
            finally:
                self._process_lock.release()

    def _retry_due(self, limit: int) -> list[SyncResult]:
        try:
            account_id = self._client.connection_id()
        except ServerConnectionRequired:
            return []
        self._recover_interrupted(account_id)
        now = _iso(self._now())
        conn = get_connection(self._db_path)
        try:
            rows = conn.execute(
                """SELECT p.operation_id FROM sync_outbox p
                   JOIN server_sync_consents c USING(consent_scope_id)
                   WHERE p.account_id=? AND p.object_type='paper' AND c.revoked_at IS NULL
                     AND p.status IN ('pending', 'retryable_failure', 'synced')
                     AND EXISTS (SELECT 1 FROM sync_outbox o
                         WHERE o.consent_scope_id=p.consent_scope_id
                           AND o.status IN ('pending', 'retryable_failure'))
                     AND NOT EXISTS (SELECT 1 FROM sync_outbox o
                         WHERE o.consent_scope_id=p.consent_scope_id AND o.status!='synced'
                           AND (o.status NOT IN ('pending', 'retryable_failure')
                                OR o.attempt_count>=5
                                OR (o.next_attempt_at IS NOT NULL AND o.next_attempt_at>?)))
                   ORDER BY p.created_at, p.operation_id LIMIT ?""",
                (account_id, now, limit),
            ).fetchall()
        finally:
            conn.close()
        results = []
        for row in rows:
            # 배치 도중 계정이 바뀌면 이전 계정의 다음 작업을 보내지 않는다.
            try:
                if self._client.connection_id() != account_id:
                    break
                results.append(self._sync(row['operation_id']))
            except (ServerConnectionRequired, KeyError):
                break
        return results

    def _recover_interrupted(self, account_id: str) -> None:
        """프로세스 잠금 보유 중에만 중단 작업을 재시도 대기로 바꾼다."""
        conn = get_connection(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """SELECT o.operation_id, o.attempt_count, o.updated_at
                   FROM sync_outbox o JOIN server_sync_consents c USING(consent_scope_id)
                   WHERE o.account_id=? AND o.status='syncing' AND c.revoked_at IS NULL
                     AND o.object_type IN ('paper', 'learning_record', 'summary', 'annotation')""",
                (account_id,),
            ).fetchall()
            now = _iso(self._now())
            for row in rows:
                try:
                    started = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
                    if started.tzinfo is None or row["attempt_count"] < 1:
                        raise ValueError
                    # 중단된 시도도 횟수에 포함하고 원래 시도 시각의 backoff를 유지한다.
                    due = _iso(started + timedelta(minutes=min(60, 2 ** min(row["attempt_count"], 5))))
                    status, code = "retryable_failure", "sync_interrupted"
                except (ValueError, TypeError, AttributeError, OverflowError):
                    due, status, code = None, "action_required", "invalid_recovery_state"
                conn.execute(
                    """UPDATE sync_outbox SET status=?, next_attempt_at=?, last_error_code=?, updated_at=?
                       WHERE operation_id=? AND status='syncing'""",
                    (status, due, code, now, row["operation_id"]),
                )
            conn.commit()
        finally:
            conn.close()

    def sync(self, operation_id: str) -> SyncResult:
        # 같은 DB를 쓰는 다른 MCP 프로세스와도 전송을 직렬화한다.
        with self._sync_lock:
            try:
                self._process_lock.acquire(timeout=0)
            except Timeout:
                return self.status(operation_id)
            try:
                return self._sync(operation_id)
            finally:
                self._process_lock.release()

    def _sync(self, operation_id: str) -> SyncResult:
        """한 작업을 전송한다. 실패해도 기존 로컬 논문 기능에는 예외를 전파하지 않는다."""
        try:
            account_id = self._client.connection_id()
        except ServerConnectionRequired:
            return SyncResult(operation_id, "action_required", error_code="reauthentication_required")
        result = self._sync_paper(operation_id, account_id)
        if result.status != "synced":
            return result
        conn = get_connection(self._db_path)
        try:
            parent = conn.execute(
                "SELECT consent_scope_id FROM sync_outbox WHERE operation_id=? AND account_id=?",
                (operation_id, account_id),
            ).fetchone()
            source_id = _source_instance_id(conn)
        finally:
            conn.close()
        self._sync_learning_records(account_id, parent["consent_scope_id"], source_id, result.server_object_id)
        self._sync_summaries(account_id, parent["consent_scope_id"], source_id, result.server_object_id)
        self._sync_annotations(account_id, parent["consent_scope_id"], source_id, result.server_object_id)
        return self.status(operation_id)

    def _sync_paper(self, operation_id: str, account_id: str) -> SyncResult:
        conn = get_connection(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT o.*, c.revoked_at FROM sync_outbox o
                   JOIN server_sync_consents c USING (consent_scope_id)
                   WHERE o.operation_id = ? AND o.account_id = ? AND o.object_type='paper'""",
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
    def _sync_learning_records(self, account_id: str, consent_id: str,
                               source_id: str, server_paper_id: str) -> None:
        conn = get_connection(self._db_path)
        try:
            rows = conn.execute(
                """SELECT operation_id FROM sync_outbox
                   WHERE account_id=? AND object_type='learning_record' AND consent_scope_id=?
                     AND status IN ('pending', 'retryable_failure', 'syncing') ORDER BY created_at, operation_id""",
                (account_id, consent_id),
            ).fetchall()
        finally:
            conn.close()
        for pending in rows:
            self._sync_learning_record(pending["operation_id"], account_id, source_id, server_paper_id)

    def _sync_learning_record(self, operation_id: str, account_id: str,
                              source_id: str, server_paper_id: str) -> None:
        conn = get_connection(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT o.*, c.revoked_at FROM sync_outbox o
                   JOIN server_sync_consents c USING(consent_scope_id)
                   WHERE o.operation_id=? AND o.account_id=? AND o.object_type='learning_record'""",
                (operation_id, account_id),
            ).fetchone()
            if row is None or row["status"] in ("synced", "conflict", "action_required"):
                return
            if row["revoked_at"] is not None:
                _finish(conn, row, "action_required", "consent_revoked", self._now())
                return
            payload = _learning_payload(conn, row["local_object_id"], row["local_subobject_id"], source_id)
            if payload is None or _payload_hash(payload) != row["payload_hash"]:
                _finish(conn, row, "conflict", "payload_changed", self._now())
                return
            attempt = row["attempt_count"] + 1
            conn.execute(
                """UPDATE sync_outbox SET status='syncing', attempt_count=attempt_count+1,
                   last_error_code=NULL, updated_at=? WHERE operation_id=?""",
                (_iso(self._now()), operation_id),
            )
            conn.commit()
        finally:
            conn.close()
        path = (f"/v1/mcp/papers/{server_paper_id}/learning-records/"
                f"{quote(row['local_subobject_id'], safe='')}")
        try:
            response = self._client.request(
                "PUT", path,
                json={key: value for key, value in payload.items() if key != "local_learning_record_id"},
                expected_connection_id=account_id,
            )
        except ServerConnectionRequired:
            self._record_failure(operation_id, account_id, attempt, "action_required", "reauthentication_required")
            return
        except Exception as exc:
            if not _is_transport_failure(exc):
                raise
            self._record_failure(operation_id, account_id, attempt, "retryable_failure",
                                 "server_unavailable", retry=True)
            return
        try:
            if response.status_code == 409:
                self._record_failure(operation_id, account_id, attempt, "conflict", "server_conflict")
                return
            if response.status_code in (404, 422):
                self._record_failure(operation_id, account_id, attempt, "action_required",
                                     "invalid_learning_record")
                return
            if response.status_code >= 500:
                self._record_failure(operation_id, account_id, attempt, "retryable_failure",
                                     "server_unavailable", retry=True)
                return
            if response.status_code != 200:
                self._record_failure(operation_id, account_id, attempt, "action_required",
                                     "unexpected_server_response")
                return
            result = _validated_learning_response(response.json())
        except (ValueError, TypeError, KeyError):
            self._record_failure(operation_id, account_id, attempt, "action_required",
                                 "invalid_server_response")
            return
        finally:
            response.close()
        conn = get_connection(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT * FROM sync_outbox WHERE operation_id=? AND account_id=?",
                (operation_id, account_id),
            ).fetchone()
            if current is None or current["attempt_count"] != attempt or current["status"] != "syncing":
                return
            now = _iso(self._now())
            conn.execute(
                """INSERT INTO server_subobject_mappings
                   (account_id, source_instance_id, object_type, local_object_id, local_subobject_id,
                    server_object_id, server_revision, synced_at)
                   VALUES (?, ?, 'learning_record', ?, ?, ?, ?, ?)
                   ON CONFLICT(account_id, source_instance_id, object_type, local_object_id, local_subobject_id)
                   DO UPDATE SET server_object_id=excluded.server_object_id,
                     server_revision=excluded.server_revision, synced_at=excluded.synced_at""",
                (account_id, source_id, current["local_object_id"], current["local_subobject_id"],
                 result["learning_record_id"], result["revision"], now),
            )
            conn.execute(
                """UPDATE sync_outbox SET status='synced', next_attempt_at=NULL,
                   last_error_code=NULL, updated_at=? WHERE operation_id=?""",
                (now, operation_id),
            )
            conn.commit()
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

    def _sync_summaries(self, account_id: str, consent_id: str,
                        source_id: str, server_paper_id: str) -> None:
        conn = get_connection(self._db_path)
        try:
            rows = conn.execute(
                """SELECT operation_id FROM sync_outbox
                   WHERE account_id=? AND object_type='summary' AND consent_scope_id=?
                     AND status IN ('pending', 'retryable_failure', 'syncing') ORDER BY created_at, operation_id""",
                (account_id, consent_id),
            ).fetchall()
        finally:
            conn.close()
        for pending in rows:
            self._sync_summary(pending["operation_id"], account_id, source_id, server_paper_id)

    def _sync_summary(self, operation_id: str, account_id: str,
                      source_id: str, server_paper_id: str) -> None:
        conn = get_connection(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT o.*, c.revoked_at FROM sync_outbox o
                   JOIN server_sync_consents c USING(consent_scope_id)
                   WHERE o.operation_id=? AND o.account_id=? AND o.object_type='summary'""",
                (operation_id, account_id),
            ).fetchone()
            if row is None or row["status"] in ("synced", "conflict", "action_required"):
                return
            if row["revoked_at"] is not None:
                _finish(conn, row, "action_required", "consent_revoked", self._now())
                return
            try:
                payload = _summary_payload(conn, row["local_object_id"], row["local_subobject_id"], source_id)
            except (TypeError, ValueError):
                payload = None
            if payload is None or _payload_hash(payload) != row["payload_hash"]:
                _finish(conn, row, "conflict", "payload_changed", self._now())
                return
            dependency = conn.execute(
                """SELECT status FROM sync_outbox WHERE account_id=? AND consent_scope_id=?
                   AND object_type='learning_record' AND local_subobject_id=?""",
                (account_id, row["consent_scope_id"], payload["local_learning_record_id"]),
            ).fetchone()
            if dependency is None or dependency["status"] != "synced":
                return
            attempt = row["attempt_count"] + 1
            conn.execute(
                """UPDATE sync_outbox SET status='syncing', attempt_count=attempt_count+1,
                   last_error_code=NULL, updated_at=? WHERE operation_id=?""",
                (_iso(self._now()), operation_id),
            )
            conn.commit()
        finally:
            conn.close()

        path = (f"/v1/mcp/papers/{server_paper_id}/learning-records/"
                f"{quote(payload['local_learning_record_id'], safe='')}/summaries/"
                f"{quote(payload['local_summary_id'], safe='')}")
        body = {key: value for key, value in payload.items()
                if key not in ("local_learning_record_id", "local_summary_id")}
        try:
            response = self._client.request("PUT", path, json=body,
                                            expected_connection_id=account_id)
        except ServerConnectionRequired:
            self._record_failure(operation_id, account_id, attempt, "action_required",
                                 "reauthentication_required")
            return
        except Exception as exc:
            if not _is_transport_failure(exc):
                raise
            self._record_failure(operation_id, account_id, attempt, "retryable_failure",
                                 "server_unavailable", retry=True)
            return
        try:
            if response.status_code == 409:
                self._record_failure(operation_id, account_id, attempt, "conflict", "server_conflict")
                return
            if response.status_code in (404, 422):
                self._record_failure(operation_id, account_id, attempt, "action_required", "invalid_summary")
                return
            if response.status_code >= 500:
                self._record_failure(operation_id, account_id, attempt, "retryable_failure",
                                     "server_unavailable", retry=True)
                return
            if response.status_code != 200:
                self._record_failure(operation_id, account_id, attempt, "action_required",
                                     "unexpected_server_response")
                return
            result = _validated_summary_response(response.json())
        except (ValueError, TypeError, KeyError):
            self._record_failure(operation_id, account_id, attempt, "action_required",
                                 "invalid_server_response")
            return
        finally:
            response.close()
        conn = get_connection(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT * FROM sync_outbox WHERE operation_id=? AND account_id=?",
                                   (operation_id, account_id)).fetchone()
            if current is None or current["attempt_count"] != attempt or current["status"] != "syncing":
                return
            now = _iso(self._now())
            conn.execute(
                """INSERT INTO server_subobject_mappings
                   (account_id, source_instance_id, object_type, local_object_id, local_subobject_id,
                    server_object_id, server_revision, synced_at)
                   VALUES (?, ?, 'summary', ?, ?, ?, ?, ?)
                   ON CONFLICT(account_id, source_instance_id, object_type, local_object_id, local_subobject_id)
                   DO UPDATE SET server_object_id=excluded.server_object_id,
                     server_revision=excluded.server_revision, synced_at=excluded.synced_at""",
                (account_id, source_id, current["local_object_id"], current["local_subobject_id"],
                 result["summary_id"], result["revision"], now),
            )
            conn.execute("""UPDATE sync_outbox SET status='synced', next_attempt_at=NULL,
                           last_error_code=NULL, updated_at=? WHERE operation_id=?""",
                         (now, operation_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _sync_annotations(self, account_id: str, consent_id: str,
                          source_id: str, server_paper_id: str) -> None:
        conn = get_connection(self._db_path)
        try:
            rows = conn.execute(
                """SELECT operation_id FROM sync_outbox
                   WHERE account_id=? AND object_type='annotation' AND consent_scope_id=?
                     AND status IN ('pending', 'retryable_failure', 'syncing')
                   ORDER BY created_at, operation_id""", (account_id, consent_id),
            ).fetchall()
        finally:
            conn.close()
        for pending in rows:
            self._sync_annotation(pending["operation_id"], account_id, source_id, server_paper_id)

    def _sync_annotation(self, operation_id: str, account_id: str,
                         source_id: str, server_paper_id: str) -> None:
        conn = get_connection(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT o.*, c.revoked_at FROM sync_outbox o
                   JOIN server_sync_consents c USING(consent_scope_id)
                   WHERE o.operation_id=? AND o.account_id=? AND o.object_type='annotation'""",
                (operation_id, account_id),
            ).fetchone()
            if row is None or row["status"] in ("synced", "conflict", "action_required"):
                return
            if row["revoked_at"] is not None:
                _finish(conn, row, "action_required", "consent_revoked", self._now())
                return
            try:
                payload = _annotation_payload(conn, row["local_object_id"],
                                              row["local_subobject_id"], source_id)
            except (TypeError, ValueError):
                payload = None
            if payload is None or _payload_hash(payload) != row["payload_hash"]:
                _finish(conn, row, "conflict", "payload_changed", self._now())
                return
            dependency = conn.execute(
                """SELECT status FROM sync_outbox WHERE account_id=? AND consent_scope_id=?
                   AND object_type='learning_record' AND local_subobject_id=?""",
                (account_id, row["consent_scope_id"], payload["local_learning_record_id"]),
            ).fetchone()
            if dependency is None or dependency["status"] != "synced":
                return
            attempt = row["attempt_count"] + 1
            conn.execute(
                """UPDATE sync_outbox SET status='syncing', attempt_count=attempt_count+1,
                   last_error_code=NULL, updated_at=? WHERE operation_id=?""",
                (_iso(self._now()), operation_id),
            )
            conn.commit()
        finally:
            conn.close()
        path = (f"/v1/mcp/papers/{server_paper_id}/learning-records/"
                f"{quote(payload['local_learning_record_id'], safe='')}/annotations/"
                f"{quote(payload['local_annotation_id'], safe='')}")
        body = {key: value for key, value in payload.items()
                if key not in ("local_learning_record_id", "local_annotation_id")}
        try:
            response = self._client.request("PUT", path, json=body,
                                            expected_connection_id=account_id)
        except ServerConnectionRequired:
            self._record_failure(operation_id, account_id, attempt, "action_required",
                                 "reauthentication_required")
            return
        except Exception as exc:
            if not _is_transport_failure(exc):
                raise
            self._record_failure(operation_id, account_id, attempt, "retryable_failure",
                                 "server_unavailable", retry=True)
            return
        try:
            if response.status_code == 409:
                self._record_failure(operation_id, account_id, attempt, "conflict", "server_conflict")
                return
            if response.status_code in (404, 422):
                self._record_failure(operation_id, account_id, attempt, "action_required",
                                     "invalid_annotation")
                return
            if response.status_code >= 500:
                self._record_failure(operation_id, account_id, attempt, "retryable_failure",
                                     "server_unavailable", retry=True)
                return
            if response.status_code != 200:
                self._record_failure(operation_id, account_id, attempt, "action_required",
                                     "unexpected_server_response")
                return
            result = _validated_annotation_response(response.json())
        except (ValueError, TypeError, KeyError):
            self._record_failure(operation_id, account_id, attempt, "action_required",
                                 "invalid_server_response")
            return
        finally:
            response.close()
        conn = get_connection(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT * FROM sync_outbox WHERE operation_id=? AND account_id=?",
                (operation_id, account_id),
            ).fetchone()
            if current is None or current["attempt_count"] != attempt or current["status"] != "syncing":
                return
            now = _iso(self._now())
            conn.execute(
                """INSERT INTO server_subobject_mappings
                   (account_id, source_instance_id, object_type, local_object_id, local_subobject_id,
                    server_object_id, server_revision, synced_at)
                   VALUES (?, ?, 'annotation', ?, ?, ?, ?, ?)
                   ON CONFLICT(account_id, source_instance_id, object_type, local_object_id, local_subobject_id)
                   DO UPDATE SET server_object_id=excluded.server_object_id,
                     server_revision=excluded.server_revision, synced_at=excluded.synced_at""",
                (account_id, source_id, current["local_object_id"], current["local_subobject_id"],
                 result["annotation_id"], result["revision"], now),
            )
            conn.execute(
                """UPDATE sync_outbox SET status='synced', next_attempt_at=NULL,
                   last_error_code=NULL, updated_at=? WHERE operation_id=?""", (now, operation_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
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
    row = _paper_row(conn, paper_id)
    if row is None:
        raise KeyError("논문을 찾을 수 없습니다.")
    return {
        "source_instance_id": source_id, "local_paper_id": row["paper_id"], "title": _effective_title(row),
        "authors": None, "publication_year": None, "original_created_at": row["created_at"],
    }


def _paper_row(conn: sqlite3.Connection, paper_id: str):
    return conn.execute(
        """SELECT p.paper_id, p.title, p.created_at,
                  (SELECT v.original_filename FROM paper_versions v
                   WHERE v.paper_id=p.paper_id
                   ORDER BY v.created_at DESC, v.version_id DESC LIMIT 1) AS original_filename
           FROM papers p WHERE p.paper_id=?""",
        (paper_id,),
    ).fetchone()


def _effective_title(row) -> str | None:
    title = row["title"]
    if isinstance(title, str) and title.strip():
        return title
    if row["original_filename"] is None:
        return None
    raw_name = str(row["original_filename"])
    file_name = PurePosixPath(PureWindowsPath(raw_name).name).name.strip()
    return file_name or None


def _learning_hash(records) -> str | None:
    if not records:
        return None
    return _payload_hash({"records": [
        {key: value for key, value in record.items() if key != "source_instance_id"}
        for record in records
    ]})


def _summary_hash(summaries) -> str | None:
    if not summaries:
        return None
    return _payload_hash({"summaries": [
        {key: value for key, value in summary.items() if key != "source_instance_id"}
        for summary in summaries
    ]})


def _annotation_hash(annotations) -> str | None:
    if not annotations:
        return None
    return _payload_hash({"annotations": [
        {key: value for key, value in annotation.items() if key != "source_instance_id"}
        for annotation in annotations
    ]})


def _latest_timestamp(values) -> str | None:
    candidates = [value for value in values if value is not None]
    if not candidates:
        return None
    try:
        return max(candidates, key=lambda value: datetime.fromisoformat(
            value.replace("Z", "+00:00")).astimezone(timezone.utc))
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise ValueError("저장된 학습 시각이 올바르지 않습니다.") from None


def _learning_payloads(conn: sqlite3.Connection, paper_id: str,
                       source_id: str) -> list[dict[str, Any]]:
    context_ids = [row["context_id"] for row in conn.execute(
        """SELECT c.context_id FROM learning_contexts c
           JOIN paper_versions v USING(version_id) WHERE v.paper_id=? ORDER BY c.context_id""",
        (paper_id,),
    ).fetchall()]
    return [payload for context_id in context_ids
            if (payload := _learning_payload(conn, paper_id, context_id, source_id)) is not None]


def _learning_payload(conn: sqlite3.Connection, paper_id: str, context_id: str,
                      source_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT c.context_id, c.version_id, c.created_at, v.original_filename,
                  lp.completed_steps, lp.resume_position, lp.last_studied_at, lp.updated_at
           FROM learning_contexts c JOIN paper_versions v USING(version_id)
           LEFT JOIN learning_progress lp USING(context_id)
           WHERE v.paper_id=? AND c.context_id=?""",
        (paper_id, context_id),
    ).fetchone()
    if row is None:
        return None
    position = json.loads(row["resume_position"]) if row["resume_position"] is not None else None
    if position is not None and not isinstance(position, dict):
        raise ValueError("저장된 학습 위치가 올바르지 않습니다.")
    if position:
        position = {key: position[key] for key in (
            "chunk_id", "pdf_page", "section_id", "section_number", "section_title", "printed_page_label"
        ) if key in position}
        detail = None
        if position.get("chunk_id") is not None:
            detail = conn.execute(
                """SELECT c.pdf_page, c.printed_page_label, c.section_id, s.title AS section_title
                   FROM chunks c LEFT JOIN sections s
                     ON s.parse_revision_id=c.parse_revision_id AND s.section_id=c.section_id
                   WHERE c.chunk_id=? AND c.parse_revision_id=(
                       SELECT parse_revision_id FROM learning_contexts WHERE context_id=?)""",
                (position["chunk_id"], context_id),
            ).fetchone()
        elif position.get("section_id") is not None:
            detail = conn.execute(
                """SELECT s.start_pdf_page AS pdf_page, NULL AS printed_page_label,
                          s.section_id, s.title AS section_title
                   FROM sections s JOIN learning_contexts c USING(parse_revision_id)
                   WHERE c.context_id=? AND s.section_id=?""",
                (context_id, position["section_id"]),
            ).fetchone()
        if detail is not None:
            for key in ("pdf_page", "printed_page_label", "section_id", "section_title"):
                if detail[key] is not None:
                    position[key] = detail[key]
    completed = json.loads(row["completed_steps"]) if row["completed_steps"] is not None else []
    if not isinstance(completed, list) or any(not isinstance(item, str) for item in completed):
        raise ValueError("저장된 완료 단계가 올바르지 않습니다.")
    raw_name = str(row["original_filename"])
    file_name = PurePosixPath(PureWindowsPath(raw_name).name).name
    return {
        "source_instance_id": source_id,
        "selected_version_id": row["version_id"],
        "file_name": file_name or None,
        "last_position": position or None,
        "last_studied_at": row["last_studied_at"],
        "completed_steps": completed,
        "source_updated_at": row["updated_at"] or row["created_at"],
        "local_learning_record_id": row["context_id"],
    }


def _summary_payloads(conn: sqlite3.Connection, paper_id: str,
                      source_id: str) -> list[dict[str, Any]]:
    summary_ids = [row["summary_id"] for row in conn.execute(
        """SELECT s.summary_id FROM summaries s
           JOIN learning_contexts c USING(context_id)
           JOIN paper_versions v USING(version_id)
           WHERE v.paper_id=? AND s.scope='paper' ORDER BY s.summary_id""",
        (paper_id,),
    ).fetchall()]
    return [payload for summary_id in summary_ids
            if (payload := _summary_payload(conn, paper_id, summary_id, source_id)) is not None]


def _summary_payload(conn: sqlite3.Connection, paper_id: str, summary_id: str,
                     source_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT s.summary_id, s.context_id, s.scope, s.created_at, c.version_id
           FROM summaries s JOIN learning_contexts c USING(context_id)
           JOIN paper_versions v USING(version_id)
           WHERE v.paper_id=? AND s.summary_id=? AND s.scope='paper'""",
        (paper_id, summary_id),
    ).fetchone()
    if row is None:
        return None
    items = []
    for item in conn.execute(
        """SELECT kind, text, evidence_ids FROM summary_items
           WHERE summary_id=? ORDER BY position""", (summary_id,),
    ).fetchall():
        evidence_ids = json.loads(item["evidence_ids"])
        if (not isinstance(evidence_ids, list) or not evidence_ids
                or any(not isinstance(value, str) or not value for value in evidence_ids)):
            raise ValueError("저장된 요약 근거 ID가 올바르지 않습니다.")
        items.append({"kind": item["kind"], "text": item["text"], "evidence_ids": evidence_ids})
    if not items:
        raise ValueError("저장된 요약 항목이 없습니다.")
    return {
        "source_instance_id": source_id,
        "selected_version_id": row["version_id"],
        "scope": row["scope"],
        "author_type": "mcp",
        "items": items,
        "original_created_at": row["created_at"],
        "source_updated_at": row["created_at"],
        "local_learning_record_id": row["context_id"],
        "local_summary_id": row["summary_id"],
    }


def _annotation_payloads(conn: sqlite3.Connection, paper_id: str,
                         source_id: str) -> list[dict[str, Any]]:
    annotation_ids = [row["annotation_id"] for row in conn.execute(
        """SELECT a.annotation_id FROM annotations a
           JOIN learning_contexts c USING(context_id)
           JOIN paper_versions v USING(version_id)
           WHERE v.paper_id=? ORDER BY a.annotation_id""", (paper_id,),
    ).fetchall()]
    return [payload for annotation_id in annotation_ids
            if (payload := _annotation_payload(conn, paper_id, annotation_id, source_id)) is not None]


def _annotation_payload(conn: sqlite3.Connection, paper_id: str, annotation_id: str,
                        source_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT a.annotation_id, a.context_id, a.kind, a.body, a.step,
                  a.created_at, a.updated_at, c.version_id
           FROM annotations a JOIN learning_contexts c USING(context_id)
           JOIN paper_versions v USING(version_id)
           WHERE v.paper_id=? AND a.annotation_id=?""", (paper_id, annotation_id),
    ).fetchone()
    if row is None:
        return None
    segments = [dict(item) for item in conn.execute(
        """SELECT chunk_id, parse_revision_id, translation_revision_id, target, start_offset, end_offset, quote_snapshot
           FROM annotation_segments WHERE annotation_id=? ORDER BY position""", (annotation_id,),
    ).fetchall()]
    return {
        "source_instance_id": source_id,
        "selected_version_id": row["version_id"],
        "kind": row["kind"],
        "body": row["body"],
        "step": row["step"],
        "segments": segments,
        "original_created_at": row["created_at"],
        "source_updated_at": row["updated_at"],
        "local_learning_record_id": row["context_id"],
        "local_annotation_id": row["annotation_id"],
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


def _validated_learning_response(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("sync_status") != "stored":
        raise ValueError
    if not isinstance(data.get("learning_record_id"), str) or not isinstance(data.get("server_updated_at"), str):
        raise ValueError
    UUID(data["learning_record_id"])
    if (not isinstance(data.get("revision"), int) or isinstance(data["revision"], bool)
            or not 1 <= data["revision"] <= 2**63 - 1):
        raise ValueError
    value = datetime.fromisoformat(data["server_updated_at"].replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError
    return data


def _validated_summary_response(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("sync_status") != "stored":
        raise ValueError
    if not isinstance(data.get("summary_id"), str) or not isinstance(data.get("server_updated_at"), str):
        raise ValueError
    UUID(data["summary_id"])
    if (not isinstance(data.get("revision"), int) or isinstance(data["revision"], bool)
            or not 1 <= data["revision"] <= 2**63 - 1):
        raise ValueError
    value = datetime.fromisoformat(data["server_updated_at"].replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError
    return data


def _validated_annotation_response(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("sync_status") != "stored":
        raise ValueError
    if not isinstance(data.get("annotation_id"), str) or not isinstance(data.get("server_updated_at"), str):
        raise ValueError
    UUID(data["annotation_id"])
    if (not isinstance(data.get("revision"), int) or isinstance(data["revision"], bool)
            or not 1 <= data["revision"] <= 2**63 - 1):
        raise ValueError
    value = datetime.fromisoformat(data["server_updated_at"].replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError
    return data


def _current_result(conn, row):
    if row["status"] != "synced":
        return SyncResult(row["operation_id"], row["status"], error_code=row["last_error_code"])
    if row["object_type"] in ("learning_record", "summary", "annotation"):
        mapping = conn.execute(
            """SELECT server_object_id FROM server_subobject_mappings
               WHERE account_id=? AND object_type=? AND local_object_id=?
               AND local_subobject_id=? AND source_instance_id=(
                   SELECT source_instance_id FROM server_sync_identity WHERE singleton=1)""",
            (row["account_id"], row["object_type"], row["local_object_id"], row["local_subobject_id"]),
        ).fetchone()
        return SyncResult(row["operation_id"], "synced", mapping["server_object_id"])
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
