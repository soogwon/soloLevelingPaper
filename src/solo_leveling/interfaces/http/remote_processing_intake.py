"""검증된 원격 PDF와 명시적 동의를 처리 작업에 원자 결합한다."""

from dataclasses import dataclass
from uuid import uuid4


class RemoteProcessingConflict(ValueError):
    pass


@dataclass(frozen=True)
class ProcessingAdmission:
    job_id: str
    paper_id: str
    server_version_id: str
    status: str


class PostgresRemoteProcessingIntake:
    def __init__(self, pool):
        self._pool = pool

    def admit(
        self,
        owner_user_id: str,
        upload_intent_id: str,
        request_key: str,
        storage_consent_id: str,
        ai_consent_id: str,
        notice_version: str,
    ) -> ProcessingAdmission | None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                owner_user_id,
                upload_intent_id,
                request_key,
                storage_consent_id,
                ai_consent_id,
                notice_version,
            )
        ):
            raise ValueError("remote_processing_input_invalid")
        job_id = str(uuid4())
        with self._pool.connection() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"{owner_user_id}\x1fprocessing\x1f{request_key}",),
            )
            existing = _existing(conn, owner_user_id, request_key)
            if existing is not None:
                _validate_existing(existing, upload_intent_id, storage_consent_id, ai_consent_id, notice_version)
                return _view(existing)
            source = conn.execute(
                """SELECT i.intent_id,i.paper_id,i.server_version_id,i.payload_hash
                   FROM server_remote_upload_intents i
                   JOIN server_pdf_uploads u
                     ON u.owner_user_id=i.owner_user_id AND u.paper_id=i.paper_id
                    AND u.server_version_id=i.server_version_id AND u.upload_id=i.upload_id
                   JOIN server_papers p
                     ON p.owner_user_id=i.owner_user_id AND p.paper_id=i.paper_id
                   WHERE i.owner_user_id=%s AND i.intent_id=%s AND i.status='verified'
                     AND u.status='ready' AND u.source_kind='remote' AND p.deleted_at IS NULL
                   FOR UPDATE OF i,u,p""",
                (owner_user_id, upload_intent_id),
            ).fetchone()
            if source is None:
                return None
            # 같은 업로드의 다른 요청 키도 원본 의도 잠금 이후 기존 작업을 재사용한다.
            existing = _existing(conn, owner_user_id, upload_intent_id, by_intent=True)
            if existing is not None:
                _validate_existing(existing, upload_intent_id, storage_consent_id, ai_consent_id, notice_version)
                return _view(existing)
            # 전송 원장과 동일하게 논문을 잠근 뒤 운영 제어를 잠근다.
            control = conn.execute(
                """SELECT generation,intake_enabled FROM server_dispatch_controls
                   WHERE control_id='provider_dispatch' FOR UPDATE"""
            ).fetchone()
            if control is None or not control["intake_enabled"]:
                return None
            consent_rows = conn.execute(
                """SELECT consent_id,purpose,input_hash FROM server_remote_consents
                   WHERE owner_user_id=%s AND consent_id IN (%s,%s)
                     AND notice_version=%s AND revoked_at IS NULL FOR SHARE""",
                (owner_user_id, storage_consent_id, ai_consent_id, notice_version),
            ).fetchall()
            consents = {row["consent_id"]: row for row in consent_rows}
            if (
                consents.get(storage_consent_id, {}).get("purpose") != "storage"
                or consents.get(ai_consent_id, {}).get("purpose") != "ai_provider"
                or consents[storage_consent_id]["input_hash"] != source["payload_hash"]
                or consents[ai_consent_id]["input_hash"] != source["payload_hash"]
            ):
                return None
            row = conn.execute(
                """INSERT INTO server_processing_jobs
                       (job_id,owner_user_id,paper_id,server_version_id,job_kind,
                        request_key,payload_hash,status)
                   VALUES (%s,%s,%s,%s,'full_processing',%s,%s,'queued')
                   RETURNING job_id,paper_id,server_version_id,status""",
                (
                    job_id,
                    owner_user_id,
                    source["paper_id"],
                    source["server_version_id"],
                    request_key,
                    source["payload_hash"],
                ),
            ).fetchone()
            conn.execute(
                """INSERT INTO server_processing_job_authorizations
                       (owner_user_id,job_id,upload_intent_id,storage_consent_id,
                        ai_consent_id,notice_version,input_hash)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (
                    owner_user_id,
                    job_id,
                    upload_intent_id,
                    storage_consent_id,
                    ai_consent_id,
                    notice_version,
                    source["payload_hash"],
                ),
            )
        return _view(row)

    def get(self, owner_user_id: str, upload_intent_id: str) -> ProcessingAdmission | None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (owner_user_id, upload_intent_id)
        ):
            raise ValueError("remote_processing_input_invalid")
        with self._pool.connection() as conn:
            row = _existing(conn, owner_user_id, upload_intent_id, by_intent=True, lock=False)
        return _view(row) if row is not None else None


def _existing(conn, owner_user_id, key, *, by_intent=False, lock=True):
    column = "a.upload_intent_id" if by_intent else "j.request_key"
    suffix = " FOR UPDATE OF j,a" if lock else ""
    return conn.execute(
        f"""SELECT j.job_id,j.paper_id,j.server_version_id,j.status,j.payload_hash,
                  a.upload_intent_id,a.storage_consent_id,a.ai_consent_id,a.notice_version
           FROM server_processing_jobs j
           JOIN server_processing_job_authorizations a
             ON a.owner_user_id=j.owner_user_id AND a.job_id=j.job_id
           WHERE j.owner_user_id=%s AND {column}=%s{suffix}""",
        (owner_user_id, key),
    ).fetchone()


class UnavailableRemoteProcessingIntake:
    def admit(self, *args, **kwargs):
        raise RuntimeError("remote_processing_unavailable")

    def get(self, *args, **kwargs):
        raise RuntimeError("remote_processing_unavailable")


def _validate_existing(row, intent_id, storage_id, ai_id, notice):
    if (str(row["upload_intent_id"]), row["storage_consent_id"], row["ai_consent_id"], row["notice_version"]) != (
        intent_id,
        storage_id,
        ai_id,
        notice,
    ):
        raise RemoteProcessingConflict("remote_processing_request_conflict")


def _view(row):
    return ProcessingAdmission(str(row["job_id"]), str(row["paper_id"]), row["server_version_id"], row["status"])
