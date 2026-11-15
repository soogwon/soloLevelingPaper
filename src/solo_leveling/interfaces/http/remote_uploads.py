"""신규 원격 PDF의 서버 ID와 업로드 의도를 멱등하게 만든다."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import uuid4

from .file_uploads import CompletedUpload, PendingUpload, UploadReservation


class RemoteUploadConflict(ValueError): pass


@dataclass(frozen=True)
class RemoteUploadIntent:
    intent_id: str
    paper_id: str
    server_version_id: str
    expires_at: datetime
    status: str


def _payload_hash(file_name, file_size, content_type, sha256_hex):
    value = json.dumps([file_name, file_size, content_type, sha256_hex], ensure_ascii=False, separators=(",", ":"))
    return sha256(value.encode()).hexdigest()


class PostgresRemoteUploadIntentStore:
    def __init__(self, pool, *, now=lambda: datetime.now(timezone.utc)):
        self._pool, self._now = pool, now

    def create(self, owner_user_id, request_key, file_name, file_size, content_type, sha256_hex):
        if not all(isinstance(v, str) and v.strip() for v in (owner_user_id, request_key, file_name, sha256_hex)):
            raise ValueError("remote_upload_input_invalid")
        if (
            request_key != request_key.strip()
            or not 1 <= len(request_key) <= 128
            or file_name != file_name.strip()
            or not 1 <= len(file_name) <= 255
            or any(ord(char) < 32 for char in file_name)
        ):
            raise ValueError("remote_upload_input_invalid")
        if content_type != "application/pdf" or type(file_size) is not int or not 1 <= file_size <= 50 * 1024 * 1024:
            raise ValueError("remote_upload_input_invalid")
        if len(sha256_hex) != 64 or any(c not in "0123456789abcdef" for c in sha256_hex):
            raise ValueError("remote_upload_input_invalid")
        now = self._now()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise ValueError("remote_upload_clock_invalid")
        digest = _payload_hash(file_name, file_size, content_type, sha256_hex)
        with self._pool.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (owner_user_id,))
            old = conn.execute(
                """SELECT intent_id,paper_id,server_version_id,expires_at,status,payload_hash
                   FROM server_remote_upload_intents WHERE owner_user_id=%s AND request_key=%s FOR UPDATE""",
                (owner_user_id, request_key),
            ).fetchone()
            if old:
                if old["payload_hash"] != digest:
                    raise RemoteUploadConflict("remote_upload_request_conflict")
                return _view(old)
            intent_id, paper_id, version_id = str(uuid4()), str(uuid4()), str(uuid4())
            expires_at = now + timedelta(minutes=15)
            conn.execute(
                """INSERT INTO server_papers(paper_id,owner_user_id,source_instance_id,local_paper_id,
                                               original_created_at,origin)
                   VALUES (%s,%s,NULL,NULL,%s,'remote')""", (paper_id, owner_user_id, now))
            conn.execute(
                """INSERT INTO server_processing_versions(owner_user_id,paper_id,server_version_id)
                   VALUES (%s,%s,%s)""", (owner_user_id, paper_id, version_id))
            row = conn.execute(
                """INSERT INTO server_remote_upload_intents
                       (intent_id,owner_user_id,paper_id,server_version_id,request_key,payload_hash,
                        file_name,file_size,content_type,sha256_hex,status,expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'created',%s)
                   RETURNING intent_id,paper_id,server_version_id,expires_at,status""",
                (intent_id, owner_user_id, paper_id, version_id, request_key, digest,
                 file_name, file_size, content_type, sha256_hex, expires_at),
            ).fetchone()
        return _view(row)

    def reserve_upload(self, owner_user_id: str, intent_id: str) -> tuple[UploadReservation, PendingUpload]:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT intent_id,paper_id,server_version_id,file_name,file_size,content_type,
                          sha256_hex,expires_at,status,upload_id
                   FROM server_remote_upload_intents
                   WHERE owner_user_id=%s AND intent_id=%s FOR UPDATE""",
                (owner_user_id, intent_id),
            ).fetchone()
            if row is None:
                raise ValueError("remote_upload_not_found")
            now = self._now()
            if row["expires_at"] <= now:
                conn.execute("UPDATE server_remote_upload_intents SET status='expired' WHERE owner_user_id=%s AND intent_id=%s",
                             (owner_user_id, intent_id))
                raise ValueError("remote_upload_expired")
            if row["status"] not in ("created", "uploading"):
                raise ValueError("remote_upload_state_invalid")
            upload_id = str(row["upload_id"] or uuid4())
            owner_hash = sha256(owner_user_id.encode()).hexdigest()[:24]
            object_key = f"private/tmp/{owner_hash}/{row['paper_id']}/{row['server_version_id']}/{upload_id}.pdf"
            if row["upload_id"] is None:
                conn.execute(
                    """INSERT INTO server_pdf_uploads
                       (upload_id,owner_user_id,paper_id,server_version_id,local_version_id,file_name,
                        file_size,content_type,sha256_hex,object_key,status,expires_at,source_kind)
                       VALUES (%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,'pending',%s,'remote')""",
                    (upload_id, owner_user_id, row["paper_id"], row["server_version_id"], row["file_name"],
                     row["file_size"], row["content_type"], row["sha256_hex"], object_key, row["expires_at"]),
                )
                conn.execute(
                    """UPDATE server_remote_upload_intents SET upload_id=%s,status='uploading',updated_at=CURRENT_TIMESTAMP
                       WHERE owner_user_id=%s AND intent_id=%s""", (upload_id, owner_user_id, intent_id))
            pending = PendingUpload(upload_id, str(row["paper_id"]), row["server_version_id"], row["file_name"],
                                    int(row["file_size"]), row["content_type"], row["sha256_hex"], object_key,
                                    row["expires_at"], "pending")
            return UploadReservation(upload_id, object_key, row["expires_at"]), pending

    def mark_verified(self, owner_user_id: str, intent_id: str, final_object_key: str) -> CompletedUpload:
        with self._pool.connection() as conn:
            row = conn.execute(
                """UPDATE server_pdf_uploads u SET status='ready',object_key=%s,completed_at=CURRENT_TIMESTAMP,
                          failure_reason=NULL,updated_at=CURRENT_TIMESTAMP
                   FROM server_remote_upload_intents i
                   WHERE i.owner_user_id=%s AND i.intent_id=%s AND i.status='uploading'
                     AND u.owner_user_id=i.owner_user_id AND u.paper_id=i.paper_id
                     AND u.server_version_id=i.server_version_id AND u.upload_id=i.upload_id
                     AND u.status='pending' AND u.expires_at>CURRENT_TIMESTAMP
                   RETURNING u.upload_id,u.paper_id,u.server_version_id,u.file_name,u.file_size,
                             u.content_type,u.sha256_hex,u.object_key,u.completed_at""",
                (final_object_key, owner_user_id, intent_id),
            ).fetchone()
            if row is None:
                raise ValueError("remote_upload_state_invalid")
            conn.execute(
                """UPDATE server_remote_upload_intents SET status='verified',updated_at=CURRENT_TIMESTAMP
                   WHERE owner_user_id=%s AND intent_id=%s""", (owner_user_id, intent_id))
        return CompletedUpload(str(row["upload_id"]), str(row["paper_id"]), row["server_version_id"],
                               row["file_name"], int(row["file_size"]), row["content_type"], row["sha256_hex"],
                               row["object_key"], row["completed_at"])


class RemoteUploadVerificationService:
    def __init__(self, store, object_storage):
        self.store, self.object_storage = store, object_storage

    def prepare(self, owner_user_id, intent_id):
        reservation, pending = self.store.reserve_upload(owner_user_id, intent_id)
        signed = self.object_storage.prepare_upload(reservation, file_size=pending.file_size,
                                                    content_type=pending.content_type, sha256_hex=pending.sha256_hex)
        return reservation, signed

    def complete(self, owner_user_id, intent_id):
        _, pending = self.store.reserve_upload(owner_user_id, intent_id)
        actual = self.object_storage.inspect_upload(pending)
        if actual is None or _object_mismatch(pending, actual):
            raise ValueError("remote_upload_object_invalid")
        final = self.object_storage.finalize_upload(pending)
        if _object_mismatch(pending, final):
            self.object_storage.delete_upload(final.object_key)
            raise ValueError("remote_upload_object_invalid")
        completed = self.store.mark_verified(owner_user_id, intent_id, final.object_key)
        self.object_storage.delete_upload(pending.object_key)
        return completed


def _object_mismatch(expected, actual):
    return (actual.file_size != expected.file_size
            or actual.content_type.split(";", 1)[0].strip().lower() != expected.content_type
            or actual.sha256_hex != expected.sha256_hex or not actual.pdf_header_valid)


def _view(row):
    return RemoteUploadIntent(str(row["intent_id"]), str(row["paper_id"]),
                              str(row["server_version_id"]), row["expires_at"], row["status"])
