"""원격 PDF 보관·AI 전송 동의를 업로드 의도에 결합한다."""

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4


NOTICE_VERSION = "remote-pdf-processing-v1"
PURPOSES = ("storage", "ai_provider")
STORAGE_NOTICE = (
    "PDF를 서버 저장소에 보관하고 처리 준비에 사용합니다. 삭제 요청 즉시 접근과 처리를 "
    "중단하며 활성 저장소와 백업의 완전 삭제에는 최대 30일이 걸릴 수 있습니다. 삭제된 "
    "자료의 복원·지연 작업 재생성을 막는 최소 삭제 표식은 추가로 최대 30일간 보관됩니다."
)
AI_NOTICE = (
    "OpenAI에 PDF에서 추출한 원문과 처리에 필요한 구간을 파싱 보완·번역·검색 색인 및 "
    "질문 답변 목적으로 전송할 수 있으며 추가 비용이 발생할 수 있습니다. PDF 파일 자체는 "
    "이 동의로 제공자에 전송하지 않습니다."
)


class RemoteUploadConsentConflict(ValueError):
    pass


@dataclass(frozen=True)
class RemoteUploadConsent:
    consent_id: str
    purpose: str
    revision: int
    notice_version: str
    granted_at: datetime
    revoked_at: datetime | None


class UnavailableRemoteUploadConsentStore:
    def list(self, *args, **kwargs):
        raise RuntimeError("remote_upload_consent_unavailable")

    def grant(self, *args, **kwargs):
        raise RuntimeError("remote_upload_consent_unavailable")

    def revoke(self, *args, **kwargs):
        raise RuntimeError("remote_upload_consent_unavailable")


class PostgresRemoteUploadConsentStore:
    def __init__(self, pool):
        self._pool = pool

    def list(self, owner_user_id: str, intent_id: str) -> tuple[RemoteUploadConsent, ...] | None:
        _validate_identity(owner_user_id, intent_id)
        with self._pool.connection() as conn:
            if not _owned_intent(conn, owner_user_id, intent_id):
                return None
            rows = conn.execute(
                """SELECT DISTINCT ON (purpose)
                          consent_id,purpose,revision,notice_version,granted_at,revoked_at
                   FROM server_remote_upload_consent_bindings
                   WHERE owner_user_id=%s AND upload_intent_id=%s
                   ORDER BY purpose,revision DESC""",
                (owner_user_id, intent_id),
            ).fetchall()
        return tuple(_view(row) for row in rows)

    def grant(
        self,
        owner_user_id: str,
        intent_id: str,
        purpose: str,
        request_key: str,
        notice_version: str = NOTICE_VERSION,
    ) -> RemoteUploadConsent | None:
        _validate(owner_user_id, intent_id, purpose, request_key, notice_version)
        with self._pool.connection() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"{owner_user_id}\x1f{intent_id}\x1f{purpose}",),
            )
            source = conn.execute(
                """SELECT payload_hash,status FROM server_remote_upload_intents i
                   WHERE owner_user_id=%s AND intent_id=%s
                     AND (i.status='verified' OR
                          (i.status IN ('created','uploading') AND i.expires_at>CURRENT_TIMESTAMP))
                     AND EXISTS (SELECT 1 FROM server_papers p
                         WHERE p.owner_user_id=i.owner_user_id AND p.paper_id=i.paper_id
                           AND p.deleted_at IS NULL)
                   FOR UPDATE""",
                (owner_user_id, intent_id),
            ).fetchone()
            if source is None:
                return None
            if purpose == "ai_provider" and source["status"] != "verified":
                raise ValueError("remote_upload_not_verified")
            replay = conn.execute(
                """SELECT b.consent_id,b.purpose,b.revision,b.notice_version,b.granted_at,b.revoked_at,
                          r.upload_intent_id,r.input_hash
                   FROM server_remote_upload_consent_requests r
                   JOIN server_remote_upload_consent_bindings b
                     ON b.owner_user_id=r.owner_user_id AND b.consent_id=r.consent_id
                   WHERE r.owner_user_id=%s AND r.purpose=%s AND r.request_key=%s
                   FOR UPDATE OF r,b""",
                (owner_user_id, purpose, request_key),
            ).fetchone()
            if replay is not None:
                if (
                    str(replay["upload_intent_id"]) != intent_id
                    or replay["notice_version"] != notice_version
                    or replay["input_hash"] != source["payload_hash"]
                ):
                    raise RemoteUploadConsentConflict("remote_upload_consent_request_conflict")
                return _view(replay)
            current = conn.execute(
                """SELECT consent_id,purpose,revision,notice_version,granted_at,revoked_at
                   FROM server_remote_upload_consent_bindings
                   WHERE owner_user_id=%s AND upload_intent_id=%s AND purpose=%s
                     AND revoked_at IS NULL FOR UPDATE""",
                (owner_user_id, intent_id, purpose),
            ).fetchone()
            if current is not None:
                if current["notice_version"] == notice_version:
                    _record_request(
                        conn, owner_user_id, intent_id, purpose, request_key,
                        current["consent_id"], source["payload_hash"], notice_version)
                    return _view(current)
                revoked = conn.execute(
                    """UPDATE server_remote_upload_consent_bindings
                       SET revoked_at=CURRENT_TIMESTAMP
                       WHERE owner_user_id=%s AND upload_intent_id=%s AND purpose=%s
                         AND consent_id=%s AND revoked_at IS NULL
                       RETURNING revoked_at""",
                    (owner_user_id, intent_id, purpose, current["consent_id"]),
                ).fetchone()
                conn.execute(
                    """UPDATE server_remote_consents SET revoked_at=%s
                       WHERE owner_user_id=%s AND consent_id=%s AND revoked_at IS NULL""",
                    (revoked["revoked_at"], owner_user_id, current["consent_id"]),
                )
            revision = int(
                conn.execute(
                    """SELECT COALESCE(max(revision),0)+1 AS revision
                       FROM server_remote_upload_consent_bindings
                       WHERE owner_user_id=%s AND upload_intent_id=%s AND purpose=%s""",
                    (owner_user_id, intent_id, purpose),
                ).fetchone()["revision"]
            )
            consent_id = str(uuid4())
            conn.execute(
                """INSERT INTO server_remote_consents
                       (consent_id,owner_user_id,purpose,input_hash,notice_version)
                   VALUES (%s,%s,%s,%s,%s)""",
                (consent_id, owner_user_id, purpose, source["payload_hash"], notice_version),
            )
            row = conn.execute(
                """INSERT INTO server_remote_upload_consent_bindings
                       (owner_user_id,upload_intent_id,purpose,revision,consent_id,notice_version)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   RETURNING consent_id,purpose,revision,notice_version,granted_at,revoked_at""",
                (owner_user_id, intent_id, purpose, revision, consent_id, notice_version),
            ).fetchone()
            _record_request(
                conn, owner_user_id, intent_id, purpose, request_key,
                consent_id, source["payload_hash"], notice_version)
        return _view(row)

    def revoke(
        self, owner_user_id: str, intent_id: str, purpose: str, expected_revision: int
    ) -> RemoteUploadConsent | None:
        _validate_identity(owner_user_id, intent_id)
        if purpose not in PURPOSES or type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("remote_upload_consent_input_invalid")
        with self._pool.connection() as conn:
            if not _owned_intent(conn, owner_user_id, intent_id):
                return None
            row = conn.execute(
                """UPDATE server_remote_upload_consent_bindings
                   SET revoked_at=CURRENT_TIMESTAMP
                   WHERE owner_user_id=%s AND upload_intent_id=%s AND purpose=%s
                     AND revision=%s AND revoked_at IS NULL
                   RETURNING consent_id,purpose,revision,notice_version,granted_at,revoked_at""",
                (owner_user_id, intent_id, purpose, expected_revision),
            ).fetchone()
            if row is None:
                latest = conn.execute(
                    """SELECT consent_id,purpose,revision,notice_version,granted_at,revoked_at
                       FROM server_remote_upload_consent_bindings
                       WHERE owner_user_id=%s AND upload_intent_id=%s AND purpose=%s
                       ORDER BY revision DESC LIMIT 1""",
                    (owner_user_id, intent_id, purpose),
                ).fetchone()
                if latest is None:
                    raise ValueError("remote_upload_consent_missing")
                if int(latest["revision"]) != expected_revision:
                    raise RemoteUploadConsentConflict("remote_upload_consent_revision_conflict")
                return _view(latest)
            conn.execute(
                """UPDATE server_remote_consents SET revoked_at=%s
                   WHERE owner_user_id=%s AND consent_id=%s AND revoked_at IS NULL""",
                (row["revoked_at"], owner_user_id, row["consent_id"]),
            )
        return _view(row)


def _owned_intent(conn, owner_user_id, intent_id):
    return conn.execute(
        """SELECT 1 FROM server_remote_upload_intents i
           WHERE i.owner_user_id=%s AND i.intent_id=%s
             AND EXISTS (SELECT 1 FROM server_papers p
                 WHERE p.owner_user_id=i.owner_user_id AND p.paper_id=i.paper_id
                   AND p.deleted_at IS NULL)""",
        (owner_user_id, intent_id),
    ).fetchone() is not None


def _record_request(conn, owner_user_id, intent_id, purpose, request_key,
                    consent_id, input_hash, notice_version):
    conn.execute(
        """INSERT INTO server_remote_upload_consent_requests
               (owner_user_id,purpose,request_key,upload_intent_id,consent_id,
                input_hash,notice_version)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (owner_user_id, purpose, request_key, intent_id, consent_id, input_hash, notice_version),
    )


def _validate_identity(owner_user_id, intent_id):
    if not all(isinstance(value, str) and value.strip() for value in (owner_user_id, intent_id)):
        raise ValueError("remote_upload_consent_input_invalid")


def _validate(owner_user_id, intent_id, purpose, request_key, notice_version):
    _validate_identity(owner_user_id, intent_id)
    if (
        purpose not in PURPOSES
        or not isinstance(request_key, str)
        or request_key != request_key.strip()
        or not 1 <= len(request_key) <= 128
        or not isinstance(notice_version, str)
        or notice_version != notice_version.strip()
        or not 1 <= len(notice_version) <= 100
    ):
        raise ValueError("remote_upload_consent_input_invalid")


def _view(row):
    return RemoteUploadConsent(
        row["consent_id"], row["purpose"], int(row["revision"]), row["notice_version"],
        row["granted_at"], row["revoked_at"],
    )
