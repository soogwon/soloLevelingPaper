"""비공개 PDF 객체 업로드 준비와 저장 한도 예약."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Protocol
from uuid import uuid4


MAX_FILE_BYTES = 50 * 1024 * 1024
DEFAULT_USER_QUOTA_BYTES = 1024 * 1024 * 1024
UPLOAD_TTL = timedelta(minutes=15)


class UploadUnavailable(Exception):
    pass


class UploadTargetNotFound(Exception):
    pass


class UploadQuotaExceeded(Exception):
    pass


class UploadAlreadyReady(Exception):
    pass


@dataclass(frozen=True)
class UploadReservation:
    upload_id: str
    object_key: str
    expires_at: datetime


class FileUploadStore(Protocol):
    def reserve(self, owner_user_id: str, paper_id: str, server_version_id: str,
                local_version_id: str, file_name: str, file_size: int,
                content_type: str, sha256_hex: str) -> UploadReservation: ...


class UnavailableFileUploadStore:
    def reserve(self, *args, **kwargs) -> UploadReservation:
        raise UploadUnavailable


class PostgresFileUploadStore:
    def __init__(self, pool: Any, *, user_quota_bytes: int = DEFAULT_USER_QUOTA_BYTES,
                 now=lambda: datetime.now(timezone.utc)):
        self._pool = pool
        self._quota = user_quota_bytes
        self._now = now

    def reserve(self, owner_user_id: str, paper_id: str, server_version_id: str,
                local_version_id: str, file_name: str, file_size: int,
                content_type: str, sha256_hex: str) -> UploadReservation:
        now = self._now()
        expires_at = now + UPLOAD_TTL
        upload_id = str(uuid4())
        owner_prefix = sha256(owner_user_id.encode("utf-8")).hexdigest()[:24]
        object_key = f"private/{owner_prefix}/{paper_id}/{server_version_id}/{upload_id}.pdf"
        with _connection(self._pool) as conn:
            # 같은 사용자의 준비 요청끼리 한도 확인과 예약이 엇갈리지 않게 직렬화한다.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (owner_user_id,))
            target = conn.execute(
                """SELECT 1 FROM server_papers p
                   WHERE p.paper_id=%s AND p.owner_user_id=%s
                     AND EXISTS (
                       SELECT 1 FROM server_learning_records r
                       WHERE r.paper_id=p.paper_id AND r.owner_user_id=p.owner_user_id
                         AND r.selected_version_id=%s
                     )""",
                (paper_id, owner_user_id, server_version_id),
            ).fetchone()
            if target is None:
                raise UploadTargetNotFound
            existing = conn.execute(
                """SELECT upload_id, object_key, expires_at, status
                   FROM server_pdf_uploads
                   WHERE owner_user_id=%s AND paper_id=%s
                     AND server_version_id=%s AND sha256_hex=%s
                   FOR UPDATE""",
                (owner_user_id, paper_id, server_version_id, sha256_hex),
            ).fetchone()
            if existing is not None and existing["status"] == "ready":
                raise UploadAlreadyReady
            if (existing is not None and existing["status"] == "pending"
                    and existing["expires_at"] > now):
                return UploadReservation(
                    str(existing["upload_id"]), existing["object_key"], existing["expires_at"])
            used = conn.execute(
                """SELECT COALESCE(sum(file_size), 0) AS used
                   FROM server_pdf_uploads
                   WHERE owner_user_id=%s AND (
                     status='ready' OR (status='pending' AND expires_at>%s)
                   )""",
                (owner_user_id, now),
            ).fetchone()
            if used is None or int(used["used"]) + file_size > self._quota:
                raise UploadQuotaExceeded
            if existing is None:
                row = conn.execute(
                    """INSERT INTO server_pdf_uploads
                   (upload_id, owner_user_id, paper_id, server_version_id, local_version_id,
                    file_name, file_size, content_type, sha256_hex, object_key, status, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
                   RETURNING upload_id, object_key, expires_at""",
                    (upload_id, owner_user_id, paper_id, server_version_id, local_version_id,
                     file_name, file_size, content_type, sha256_hex, object_key, expires_at),
                ).fetchone()
            else:
                row = conn.execute(
                    """UPDATE server_pdf_uploads SET
                         upload_id=%s, local_version_id=%s, file_name=%s, file_size=%s,
                         content_type=%s, object_key=%s, status='pending', failure_reason=NULL,
                         expires_at=%s, updated_at=CURRENT_TIMESTAMP
                       WHERE owner_user_id=%s AND paper_id=%s
                         AND server_version_id=%s AND sha256_hex=%s
                       RETURNING upload_id, object_key, expires_at""",
                    (upload_id, local_version_id, file_name, file_size, content_type, object_key,
                     expires_at, owner_user_id, paper_id, server_version_id, sha256_hex),
                ).fetchone()
            if row is None:
                raise UploadUnavailable
            return UploadReservation(str(row["upload_id"]), row["object_key"], row["expires_at"])


@contextmanager
def _connection(pool):
    with pool.connection() as conn:
        with conn.transaction():
            yield conn

