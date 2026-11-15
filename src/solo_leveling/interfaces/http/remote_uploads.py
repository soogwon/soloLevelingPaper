"""신규 원격 PDF의 서버 ID와 업로드 의도를 멱등하게 만든다."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import uuid4


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
        if content_type != "application/pdf" or type(file_size) is not int or not 1 <= file_size <= 50 * 1024 * 1024:
            raise ValueError("remote_upload_input_invalid")
        if len(sha256_hex) != 64 or any(c not in "0123456789abcdef" for c in sha256_hex):
            raise ValueError("remote_upload_input_invalid")
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
            expires_at = self._now() + timedelta(minutes=15)
            conn.execute(
                """INSERT INTO server_papers(paper_id,owner_user_id,source_instance_id,local_paper_id,
                                               original_created_at,origin)
                   VALUES (%s,%s,NULL,NULL,%s,'remote')""", (paper_id, owner_user_id, self._now()))
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


def _view(row):
    return RemoteUploadIntent(str(row["intent_id"]), str(row["paper_id"]),
                              str(row["server_version_id"]), row["expires_at"], row["status"])
