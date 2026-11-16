"""원격 처리 큐의 현재 실행 세대를 검증된 R2 입력과 B 처리 단계에 연결한다."""

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol
from uuid import uuid4

from solo_leveling.domain.remote_processing import ProcessingResult, ProcessingSource
from solo_leveling.interfaces.http.processing_jobs import ProcessingJob


PROCESSING_PHASES = (
    "queued", "input_validation", "parsing", "translating", "embedding", "publishing", "finished",
)


class RemoteProcessingInputRejected(RuntimeError):
    """현재 owner/lease/동의/원본 범위에서 실행할 수 없다."""


@dataclass(frozen=True, repr=False)
class RemoteProcessingInput:
    source: ProcessingSource
    object_key: str
    content_type: str


@dataclass(frozen=True, repr=False)
class ClaimedProcessingResult:
    job: ProcessingJob
    result: ProcessingResult


class JobQueue(Protocol):
    def claim(self, worker_id: str, *, lease_seconds: int) -> ProcessingJob | None: ...


class ProcessingInputStore(Protocol):
    def load(self, job: ProcessingJob) -> RemoteProcessingInput: ...
    def allowed(self, source: ProcessingSource, phase: str) -> bool: ...


class ObjectReader(Protocol):
    def read_object(self, object_key: str, *, expected_size: int): ...


class ProcessingStages(Protocol):
    def run(self, source: ProcessingSource, pdf: bytes) -> ProcessingResult: ...


class PostgresRemoteProcessingInputStore:
    """job authorization과 현재 원본·동의·lease를 한 owner 범위에서 읽는다."""

    def __init__(self, pool):
        self._pool = pool

    def load(self, job: ProcessingJob) -> RemoteProcessingInput:
        if (
            not isinstance(job, ProcessingJob)
            or job.status != "running"
            or job.job_kind != "full_processing"
            or not job.lease_token
        ):
            raise ValueError("remote_processing_job_invalid")
        with self._pool.connection() as conn:
            row = conn.execute(_SOURCE_SQL + " FOR SHARE OF j,i,u,p,sb,ab,sc,ac", (
                job.owner_user_id, job.job_id, job.lease_token,
            )).fetchone()
        if row is None:
            raise RemoteProcessingInputRejected("remote_processing_input_rejected")
        owner_hash = sha256(job.owner_user_id.encode("utf-8")).hexdigest()[:24]
        expected_prefix = (
            f"private/files/{owner_hash}/{job.paper_id}/{job.server_version_id}/"
        )
        if not row["object_key"].startswith(expected_prefix):
            raise RemoteProcessingInputRejected("remote_processing_object_key_rejected")
        source = ProcessingSource(
            job.owner_user_id, job.job_id, job.paper_id, job.server_version_id,
            str(uuid4()), str(uuid4()), str(uuid4()), row["sha256_hex"], int(row["file_size"]),
            job.lease_token,
        )
        return RemoteProcessingInput(source, row["object_key"], row["content_type"])

    def allowed(self, source: ProcessingSource, phase: str) -> bool:
        if not isinstance(source, ProcessingSource) or not source.lease_token or phase not in (
            "parse", "translation", "embedding", "result",
        ):
            return False
        with self._pool.connection() as conn:
            row = conn.execute(
                _SOURCE_SQL + " AND (%s<>'translation' OR ctl.dispatch_enabled=true)",
                (source.owner_user_id, source.job_id, source.lease_token, phase),
            ).fetchone()
        return row is not None


class RemoteProcessingWorker:
    """한 작업을 claim하고 검증된 bytes로 처리한다. 게시 전에는 job을 성공 처리하지 않는다."""

    def __init__(self, jobs: JobQueue, inputs: ProcessingInputStore, objects: ObjectReader,
                 stages: ProcessingStages, *, worker_id: str, lease_seconds: int = 60):
        if not isinstance(worker_id, str) or not worker_id.strip() or type(lease_seconds) is not int or lease_seconds < 1:
            raise ValueError("remote_processing_worker_config_invalid")
        self.jobs, self.inputs, self.objects, self.stages = jobs, inputs, objects, stages
        self.worker_id, self.lease_seconds = worker_id, lease_seconds

    def run_once(self) -> ClaimedProcessingResult | None:
        job = self.jobs.claim(self.worker_id, lease_seconds=self.lease_seconds)
        if job is None:
            return None
        prepared = self.inputs.load(job)
        if self.inputs.allowed(prepared.source, "parse") is not True:
            raise RemoteProcessingInputRejected("remote_processing_input_rejected")
        downloaded = self.objects.read_object(
            prepared.object_key, expected_size=prepared.source.pdf_size,
        )
        if (
            downloaded.object_key != prepared.object_key
            or downloaded.content_type != prepared.content_type
            or downloaded.file_size != prepared.source.pdf_size
            or downloaded.sha256_hex != prepared.source.pdf_sha256
            or not downloaded.pdf_header_valid
            or type(downloaded.body) is not bytes
        ):
            raise RemoteProcessingInputRejected("remote_processing_object_mismatch")
        if self.inputs.allowed(prepared.source, "parse") is not True:
            raise RemoteProcessingInputRejected("remote_processing_input_rejected")
        result = self.stages.run(prepared.source, downloaded.body)
        if not isinstance(result, ProcessingResult) or result.source != prepared.source:
            raise RuntimeError("remote_processing_result_invalid")
        return ClaimedProcessingResult(job, result)


_SOURCE_SQL = """SELECT u.object_key,u.file_size,u.content_type,u.sha256_hex
   FROM server_processing_jobs j
   JOIN server_processing_job_authorizations a
     ON a.owner_user_id=j.owner_user_id AND a.job_id=j.job_id
   JOIN server_remote_upload_intents i
     ON i.owner_user_id=a.owner_user_id AND i.intent_id=a.upload_intent_id
   JOIN server_pdf_uploads u
     ON u.owner_user_id=i.owner_user_id AND u.paper_id=i.paper_id
    AND u.server_version_id=i.server_version_id AND u.upload_id=i.upload_id
   JOIN server_papers p
     ON p.owner_user_id=j.owner_user_id AND p.paper_id=j.paper_id
   JOIN server_remote_upload_consent_bindings sb
     ON sb.owner_user_id=a.owner_user_id AND sb.upload_intent_id=a.upload_intent_id
    AND sb.purpose='storage' AND sb.consent_id=a.storage_consent_id AND sb.revoked_at IS NULL
   JOIN server_remote_upload_consent_bindings ab
     ON ab.owner_user_id=a.owner_user_id AND ab.upload_intent_id=a.upload_intent_id
    AND ab.purpose='ai_provider' AND ab.consent_id=a.ai_consent_id AND ab.revoked_at IS NULL
   JOIN server_remote_consents sc
     ON sc.owner_user_id=sb.owner_user_id AND sc.consent_id=sb.consent_id AND sc.revoked_at IS NULL
   JOIN server_remote_consents ac
     ON ac.owner_user_id=ab.owner_user_id AND ac.consent_id=ab.consent_id AND ac.revoked_at IS NULL
   JOIN server_dispatch_controls ctl ON ctl.control_id='provider_dispatch'
   WHERE j.owner_user_id=%s AND j.job_id=%s AND j.status='running' AND j.job_kind='full_processing'
     AND j.lease_token=%s AND j.lease_expires_at>CURRENT_TIMESTAMP
     AND j.paper_id=i.paper_id AND j.server_version_id=i.server_version_id
     AND j.payload_hash=i.payload_hash AND a.input_hash=i.payload_hash
     AND i.status='verified' AND u.status='ready' AND u.source_kind='remote'
     AND u.object_key LIKE 'private/files/%%' AND u.file_size=i.file_size
     AND u.content_type=i.content_type AND u.sha256_hex=i.sha256_hex
     AND i.content_type='application/pdf' AND p.deleted_at IS NULL
     AND a.notice_version=sb.notice_version AND a.notice_version=ab.notice_version
     AND sc.purpose='storage' AND ac.purpose='ai_provider'
     AND sc.input_hash=i.payload_hash AND ac.input_hash=i.payload_hash
     AND sc.notice_version=a.notice_version AND ac.notice_version=a.notice_version"""
