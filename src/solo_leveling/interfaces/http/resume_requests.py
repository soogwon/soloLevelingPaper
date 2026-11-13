"""웹에서 만든 학습 이어하기 요청을 인증된 로컬 MCP가 해석한다."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import secrets
from typing import Any, Protocol
from uuid import uuid4


class ResumeRequestNotFound(Exception):
    pass


class ResumeRequestConflict(Exception):
    pass


class ResumeRequestExpired(Exception):
    pass


class ResumeRequestStoreUnavailable(Exception):
    pass


@dataclass(frozen=True)
class ResumeRequestResult:
    resume_request_id: str
    code: str
    expires_at: datetime
    prompt_text: str
    status: str
    reason: str | None


@dataclass(frozen=True)
class ResumeResolution:
    resume_request_id: str
    paper_id: str
    title: str | None
    version_id: str
    learning_record_id: str
    last_position: dict[str, Any] | None
    annotation_id: str | None
    annotation: dict[str, Any] | None
    file_name: str | None
    file_access_status: str
    status: str
    reason: str | None


class ResumeRequestStore(Protocol):
    def create(self, owner_user_id: str, idempotency_key: str, *, paper_id: str,
               version_id: str, learning_record_id: str,
               annotation_id: str | None) -> ResumeRequestResult: ...

    def resolve(self, owner_user_id: str, code: str) -> ResumeResolution: ...


class UnavailableResumeRequestStore:
    def create(self, *args, **kwargs):
        raise ResumeRequestStoreUnavailable

    def resolve(self, *args, **kwargs):
        raise ResumeRequestStoreUnavailable


class PostgresResumeRequestStore:
    def __init__(self, pool: Any):
        self._pool = pool

    def create(self, owner_user_id: str, idempotency_key: str, *, paper_id: str,
               version_id: str, learning_record_id: str,
               annotation_id: str | None) -> ResumeRequestResult:
        payload_hash = _payload_hash(paper_id, version_id, learning_record_id, annotation_id)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=10)
        request_id = str(uuid4())
        code = secrets.token_urlsafe(9)
        prompt = f"Solo Leveling에 저장된 학습 기록을 이어서 공부할게. 이어하기 코드: {code}"
        try:
            with _connection(self._pool) as conn:
                def previous_request():
                    return conn.execute(
                        """SELECT payload_hash, resume_request_id, code, expires_at, prompt_text,
                              status, reason
                       FROM server_resume_requests
                       WHERE owner_user_id=%s AND idempotency_key=%s""",
                        (owner_user_id, idempotency_key),
                    ).fetchone()

                def replay(row):
                    if bytes(row["payload_hash"]) != payload_hash:
                        raise ResumeRequestConflict
                    return _result(row)

                previous = previous_request()
                if previous is not None:
                    return replay(previous)
                record = conn.execute(
                    """SELECT p.title, r.last_position, r.file_name
                       FROM server_learning_records r
                       JOIN server_papers p ON p.paper_id=r.paper_id AND p.owner_user_id=r.owner_user_id
                       WHERE r.owner_user_id=%s AND r.paper_id=%s AND r.learning_record_id=%s
                         AND r.selected_version_id=%s""",
                    (owner_user_id, paper_id, learning_record_id, version_id),
                ).fetchone()
                if record is None:
                    raise ResumeRequestNotFound
                if annotation_id is not None:
                    annotation = conn.execute(
                        """SELECT 1 FROM server_annotations
                           WHERE owner_user_id=%s AND annotation_id=%s AND paper_id=%s
                             AND learning_record_id=%s AND selected_version_id=%s""",
                        (owner_user_id, annotation_id, paper_id, learning_record_id, version_id),
                    ).fetchone()
                    if annotation is None:
                        raise ResumeRequestNotFound
                inserted = conn.execute(
                    """INSERT INTO server_resume_requests
                       (resume_request_id, owner_user_id, idempotency_key, payload_hash, code,
                        paper_id, version_id, learning_record_id, annotation_id, expires_at,
                        prompt_text, status, reason)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'created','local_client_required')
                       ON CONFLICT (owner_user_id, idempotency_key) DO NOTHING
                       RETURNING resume_request_id""",
                    (request_id, owner_user_id, idempotency_key, payload_hash, code, paper_id,
                     version_id, learning_record_id, annotation_id, expires_at, prompt),
                ).fetchone()
                if inserted is None:
                    return replay(previous_request())
        except (ResumeRequestConflict, ResumeRequestNotFound):
            raise
        except Exception as exc:
            _translate_store_error(exc)
        return ResumeRequestResult(request_id, code, expires_at, prompt, "created",
                                   "local_client_required")

    def resolve(self, owner_user_id: str, code: str) -> ResumeResolution:
        try:
            with _connection(self._pool) as conn:
                row = conn.execute(
                    """SELECT q.resume_request_id, q.paper_id, p.title, q.version_id,
                              q.learning_record_id, r.last_position, q.annotation_id,
                              a.kind AS annotation_kind, a.body AS annotation_body,
                              a.segments AS annotation_segments, r.file_name, q.expires_at
                       FROM server_resume_requests q
                       JOIN server_papers p ON p.paper_id=q.paper_id AND p.owner_user_id=q.owner_user_id
                       JOIN server_learning_records r
                         ON r.learning_record_id=q.learning_record_id AND r.paper_id=q.paper_id
                        AND r.owner_user_id=q.owner_user_id AND r.selected_version_id=q.version_id
                       LEFT JOIN server_annotations a
                         ON a.annotation_id=q.annotation_id AND a.owner_user_id=q.owner_user_id
                       WHERE q.owner_user_id=%s AND q.code=%s""",
                    (owner_user_id, code),
                ).fetchone()
        except Exception as exc:
            _translate_store_error(exc)
        if row is None:
            raise ResumeRequestNotFound
        if row["expires_at"] <= datetime.now(timezone.utc):
            raise ResumeRequestExpired
        annotation = None
        if row["annotation_id"] is not None:
            annotation = {
                "annotation_id": str(row["annotation_id"]),
                "kind": row["annotation_kind"],
                "body": row["annotation_body"],
                "segments": list(row["annotation_segments"] or ()),
            }
        return ResumeResolution(
            str(row["resume_request_id"]), str(row["paper_id"]), row["title"],
            row["version_id"], str(row["learning_record_id"]), row["last_position"],
            str(row["annotation_id"]) if row["annotation_id"] is not None else None,
            annotation, row["file_name"], "local_check_required", "prepared",
            "local_file_required",
        )


def _payload_hash(paper_id: str, version_id: str, learning_record_id: str,
                  annotation_id: str | None) -> bytes:
    value = json.dumps(
        {"paper_id": paper_id, "version_id": version_id,
         "learning_record_id": learning_record_id, "annotation_id": annotation_id},
        sort_keys=True, separators=(",", ":"),
    )
    return sha256(value.encode()).digest()


def _result(row) -> ResumeRequestResult:
    return ResumeRequestResult(
        str(row["resume_request_id"]), row["code"], row["expires_at"],
        row["prompt_text"], row["status"], row["reason"],
    )


@contextmanager
def _connection(pool: Any):
    with pool.connection() as conn:
        yield conn


def _translate_store_error(exc: Exception):
    try:
        from psycopg import Error
        from psycopg_pool import PoolTimeout
        if isinstance(exc, (Error, PoolTimeout)):
            raise ResumeRequestStoreUnavailable from None
    except ImportError:
        pass
    raise exc
