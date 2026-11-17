"""원격 처리 큐의 현재 실행 세대를 검증된 R2 입력과 B 처리 단계에 연결한다."""

from dataclasses import dataclass
from hashlib import sha256
from threading import Event, Lock, Thread
from typing import Protocol
from uuid import uuid4

from solo_leveling.domain.remote_processing import ProcessingResult, ProcessingSource
from solo_leveling.interfaces.http.processing_jobs import ProcessingJob, ProcessingJobLeaseLost


PROCESSING_PHASES = (
    "queued", "input_validation", "parsing", "translating", "embedding", "publishing", "finished",
)


class RemoteProcessingInputRejected(RuntimeError):
    """현재 owner/lease/동의/원본 범위에서 실행할 수 없다."""


class RemoteProcessingLeaseLost(RuntimeError):
    """heartbeat 실패로 현재 실행 세대의 결과를 사용할 수 없다."""


class RemoteProcessingHeartbeatFailed(RuntimeError):
    """heartbeat 저장소를 신뢰성 있게 호출하거나 종료할 수 없다."""


@dataclass(frozen=True, repr=False)
class RemoteProcessingInput:
    source: ProcessingSource
    object_key: str
    content_type: str


@dataclass(frozen=True, repr=False)
class ClaimedProcessingResult:
    job: ProcessingJob
    result: ProcessingResult
    staged: object | None = None


class JobQueue(Protocol):
    def claim(self, worker_id: str, *, lease_seconds: int) -> ProcessingJob | None: ...
    def heartbeat(self, owner_user_id: str, job_id: str, lease_token: str,
                  *, lease_seconds: int) -> None: ...


class ProcessingInputStore(Protocol):
    def load(self, job: ProcessingJob) -> RemoteProcessingInput: ...
    def allowed(self, source: ProcessingSource, phase: str) -> bool: ...


class ObjectReader(Protocol):
    def read_object(self, object_key: str, *, expected_size: int): ...


class ProcessingStages(Protocol):
    def run(self, source: ProcessingSource, pdf: bytes) -> ProcessingResult: ...


class _LeaseHeartbeat:
    """blocking 처리 중 별도 thread에서 lease를 연장하고 실패를 보존한다."""

    def __init__(self, jobs: JobQueue, job: ProcessingJob, *, lease_seconds: int,
                 interval_seconds: float):
        self._jobs, self._job = jobs, job
        self._lease_seconds, self._interval_seconds = lease_seconds, interval_seconds
        self._stop, self._lock = Event(), Lock()
        self._failure: Exception | None = None
        self._thread = Thread(
            target=self._run, name="remote-processing-heartbeat", daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self._jobs.heartbeat(
                    self._job.owner_user_id, self._job.job_id, self._job.lease_token,
                    lease_seconds=self._lease_seconds,
                )
            except Exception as exc:
                with self._lock:
                    self._failure = exc
                self._stop.set()
                return

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=min(1.0, max(0.1, self._interval_seconds * 2)))
        if self._thread.is_alive():
            with self._lock:
                self._failure = RuntimeError("heartbeat_stop_timeout")

    def check(self) -> None:
        with self._lock:
            failure = self._failure
        if isinstance(failure, ProcessingJobLeaseLost):
            raise RemoteProcessingLeaseLost("remote_processing_lease_lost") from None
        if failure is not None:
            # DB/driver 원문이나 자격 증명이 상위 공개 오류로 유출되지 않게 고정한다.
            raise RemoteProcessingHeartbeatFailed("remote_processing_heartbeat_failed") from None


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
                 stages: ProcessingStages, *, worker_id: str, lease_seconds: int = 60,
                 heartbeat_interval_seconds: float | None = None, stages_factory=None, result_store=None):
        if not isinstance(worker_id, str) or not worker_id.strip() or type(lease_seconds) is not int or lease_seconds < 1:
            raise ValueError("remote_processing_worker_config_invalid")
        interval = lease_seconds / 3 if heartbeat_interval_seconds is None else heartbeat_interval_seconds
        if (type(interval) not in (int, float) or not 0 < interval < lease_seconds):
            raise ValueError("remote_processing_worker_config_invalid")
        if stages_factory is not None and not callable(stages_factory):
            raise ValueError("remote_processing_worker_config_invalid")
        if result_store is not None and not callable(getattr(result_store, "save", None)):
            raise ValueError("remote_processing_worker_config_invalid")
        self.result_store = result_store
        self.stages_factory = stages_factory
        self.jobs, self.inputs, self.objects, self.stages = jobs, inputs, objects, stages
        self.worker_id, self.lease_seconds = worker_id, lease_seconds
        self.heartbeat_interval_seconds = float(interval)

    def run_once(self) -> ClaimedProcessingResult | None:
        job = self.jobs.claim(self.worker_id, lease_seconds=self.lease_seconds)
        if job is None:
            return None
        if not job.lease_token:
            raise RemoteProcessingLeaseLost("remote_processing_lease_lost")
        heartbeat = _LeaseHeartbeat(
            self.jobs, job, lease_seconds=self.lease_seconds,
            interval_seconds=self.heartbeat_interval_seconds,
        )
        heartbeat.start()
        result = None
        staged = None
        failure = None
        try:
            prepared = self.inputs.load(job)
            heartbeat.check()
            if self.inputs.allowed(prepared.source, "parse") is not True:
                raise RemoteProcessingInputRejected("remote_processing_input_rejected")
            downloaded = self.objects.read_object(
                prepared.object_key, expected_size=prepared.source.pdf_size,
            )
            heartbeat.check()
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
            def check_allowed(current, phase):
                heartbeat.check()
                if current != prepared.source:
                    return False
                allowed = self.inputs.allowed(current, phase)
                heartbeat.check()
                return allowed is True

            # 실행별로 조립하여 다른 작업의 callback이나 heartbeat를 공유하지 않는다.
            stages = self.stages if self.stages_factory is None else self.stages_factory(
                prepared.source, check_allowed,
            )
            if not callable(getattr(stages, "run", None)):
                raise RuntimeError("remote_processing_stages_invalid")
            result = stages.run(prepared.source, downloaded.body)
            heartbeat.check()
            if not isinstance(result, ProcessingResult) or result.source != prepared.source:
                raise RuntimeError("remote_processing_result_invalid")
            if self.result_store is not None and result.status == "prepared":
                if check_allowed(result.source, "result") is not True:
                    raise RemoteProcessingInputRejected("remote_processing_input_rejected")
                staged = self.result_store.save(result)
                heartbeat.check()
        except Exception as exc:
            failure = exc
        finally:
            heartbeat.stop()
        # 처리 오류와 동시에 heartbeat가 실패했다면 stale 결과 폐기를 우선한다.
        heartbeat.check()
        if failure is not None:
            raise failure
        return ClaimedProcessingResult(job, result, staged)


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
