"""검증된 원격 PDF와 명시적 동의를 처리 작업에 원자 결합한다."""

from dataclasses import dataclass
from uuid import uuid4

from .automatic_pdf_budgets import AutomaticPdfQuotaExceeded


class RemoteProcessingConflict(ValueError):
    pass


class RemoteProcessingQuotaExceeded(Exception):
    """계정당 처리 접수 건수 한도에 도달했다."""


@dataclass(frozen=True)
class ProcessingAdmission:
    job_id: str
    paper_id: str
    server_version_id: str
    status: str


class PostgresRemoteProcessingIntake:
    def __init__(self, pool, *, automatic_pdf_budget=None):
        self._pool = pool
        self._automatic_pdf_budget = automatic_pdf_budget

    def quota_reached(self, owner_user_id: str) -> bool:
        """새 처리 접수가 계정당 건수 한도에 막히는지 읽기만 한다."""
        budget = self._automatic_pdf_budget
        if budget is None or budget.max_jobs_per_owner is None:
            return False
        with self._pool.connection() as conn:
            return budget.quota_reached(conn, owner_user_id)

    def find_source(self, owner_user_id: str, paper_id: str, version_id: str):
        with self._pool.connection() as conn:
            return conn.execute(
                """SELECT i.intent_id, v.active_publication_id
                   FROM server_remote_upload_intents i
                   JOIN server_pdf_uploads u
                     ON u.owner_user_id=i.owner_user_id AND u.paper_id=i.paper_id
                    AND u.server_version_id=i.server_version_id AND u.upload_id=i.upload_id
                   JOIN server_papers p
                     ON p.owner_user_id=i.owner_user_id AND p.paper_id=i.paper_id
                   JOIN server_processing_versions v
                     ON v.owner_user_id=i.owner_user_id AND v.paper_id=i.paper_id
                    AND v.server_version_id=i.server_version_id
                   WHERE i.owner_user_id=%s AND i.paper_id=%s AND i.server_version_id=%s
                     AND i.status='verified' AND u.status='ready' AND u.source_kind='remote'
                     AND p.deleted_at IS NULL
                   ORDER BY i.updated_at DESC, i.intent_id LIMIT 1""",
                (owner_user_id, paper_id, version_id),
            ).fetchone()

    def admit(
        self,
        owner_user_id: str,
        upload_intent_id: str,
        request_key: str,
        storage_consent_id: str,
        ai_consent_id: str,
        notice_version: str,
        *,
        previous_job_id: str | None = None,
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
                if _previous_job_id(existing) != previous_job_id:
                    raise RemoteProcessingConflict("remote_processing_request_conflict")
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
            # 업로드 잠금으로 새 키의 동시 접수와 명시적 후속 접수를 직렬화한다.
            existing = _existing(conn, owner_user_id, upload_intent_id, by_intent=True)
            if previous_job_id is not None:
                if not _can_readmit(conn, owner_user_id, previous_job_id, source, existing):
                    return None
            elif existing is not None:
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
            budget = self._automatic_pdf_budget
            if budget is not None:
                try:
                    allowed = budget.allows_admission(conn, owner_user_id)
                except AutomaticPdfQuotaExceeded:
                    raise RemoteProcessingQuotaExceeded('remote_processing_quota_exceeded') from None
                if not allowed:
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
                        ai_consent_id,notice_version,input_hash,previous_job_id,
                        processing_policy_version,processing_window_key)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    owner_user_id,
                    job_id,
                    upload_intent_id,
                    storage_consent_id,
                    ai_consent_id,
                    notice_version,
                    source["payload_hash"],
                    previous_job_id,
                    budget.policy_version if budget else None,
                    budget.window_key if budget else None,
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
    order = " ORDER BY (a.previous_job_id IS NOT NULL) DESC LIMIT 1" if by_intent else ""
    suffix = " FOR UPDATE OF j,a" if lock else ""
    return conn.execute(
        f"""SELECT j.job_id,j.paper_id,j.server_version_id,j.status,j.payload_hash,
                  a.upload_intent_id,a.storage_consent_id,a.ai_consent_id,a.notice_version,
                  a.previous_job_id,j.job_kind,j.error_code
           FROM server_processing_jobs j
           JOIN server_processing_job_authorizations a
             ON a.owner_user_id=j.owner_user_id AND a.job_id=j.job_id
           WHERE j.owner_user_id=%s AND {column}=%s
             AND EXISTS (SELECT 1 FROM server_papers p
                 WHERE p.owner_user_id=j.owner_user_id AND p.paper_id=j.paper_id
                   AND p.deleted_at IS NULL){order}{suffix}""",
        (owner_user_id, key),
    ).fetchone()


def _previous_job_id(row):
    value = row.get("previous_job_id")
    return str(value) if value is not None else None


def _can_readmit(conn, owner_user_id, previous_job_id, source, existing):
    if (
        existing is None
        or str(existing["job_id"]) != previous_job_id
        or _previous_job_id(existing) is not None
        or existing["job_kind"] != "full_processing"
        or existing["status"] != "failed"
        or existing["error_code"] != "translation_incomplete"
        or existing["payload_hash"] != source["payload_hash"]
    ):
        return False
    # 기존 게시본, 복구할 결과 또는 미확정 사용량이 있으면 새 전송을 접수하지 않는다.
    return conn.execute(
        """SELECT 1 FROM server_processing_versions v
           WHERE v.owner_user_id=%s AND v.paper_id=%s AND v.server_version_id=%s
             AND v.active_publication_id IS NULL
             AND NOT EXISTS (
                 SELECT 1 FROM server_processing_result_staging s
                 WHERE s.owner_user_id=v.owner_user_id AND s.job_id=%s
             )
             AND NOT EXISTS (
                 SELECT 1 FROM server_processing_provider_attempts a
                 WHERE a.owner_user_id=v.owner_user_id AND a.execution_id=%s
                   AND (a.status<>'observed'
                        OR a.actual_input_tokens>a.reserved_input_tokens
                        OR a.actual_output_tokens>a.reserved_output_tokens)
             )""",
        (owner_user_id, source["paper_id"], source["server_version_id"],
         previous_job_id, previous_job_id),
    ).fetchone() is not None


class UnavailableRemoteProcessingIntake:
    def find_source(self, *args, **kwargs):
        raise RuntimeError("remote_processing_unavailable")

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
