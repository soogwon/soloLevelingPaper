"""로컬 PDF 등록을 백그라운드 ingestion 작업에 연결한다."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import threading
import math
import uuid

from solo_leveling.application.translation.service import TranslationService
from solo_leveling.domain.models import IngestionDisposition, JobStatus
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.workers.ingestion import (
    get_ingestion_status, prepare_local_ingestion, run_prepared_local_ingestion,
)
from .local_files import LocalPdfStore


class IngestionShuttingDownError(RuntimeError):
    """종료를 시작한 등록 관리자는 새 요청을 받지 않는다."""


@dataclass(frozen=True)
class IngestionServices:
    translation: TranslationService
    translation_settings: TranslationSettings
    embedder: object = None
    chroma_client: object = None


class LocalIngestionManager:
    def __init__(self, db_path: str, chroma_dir: str, pdf_store: LocalPdfStore,
                 services: IngestionServices, *, max_workers: int = 1):
        self.db_path = db_path
        self.chroma_dir = chroma_dir
        self.pdf_store = pdf_store
        self.services = services
        self.executor = ThreadPoolExecutor(max_workers=max_workers,
                                           thread_name_prefix='paper-ingestion')
        # 이미 완료된 Future의 콜백이 제출 스레드에서 즉시 실행될 수 있다.
        self.lock = threading.RLock()
        self._condition = threading.Condition(self.lock)
        self._accepting = True
        self._pending = {}

    def _on_done(self, future):
        # 취소된 Future에서는 exception()이 CancelledError를 발생시킨다.
        try:
            if not future.cancelled():
                future.exception()
        finally:
            with self._condition:
                self._pending.pop(future, None)
                self._condition.notify_all()

    def begin_shutdown(self) -> None:
        """시작 전 작업만 취소하고 실행 중인 작업은 완료까지 유지한다."""
        with self.lock:
            if not self._accepting:
                return
            self._accepting = False
            # cancel()의 즉시 콜백이 원본 사전을 바꾸므로 사본을 순회한다.
            for future, job_id in list(self._pending.items()):
                if future.cancel():
                    try:
                        repo.mark_job_cancelled_before_start(self.db_path, job_id)
                    except Exception:
                        from .lifecycle import report
                        report('INGESTION_CANCEL_STATE_FAILED')
            self.executor.shutdown(wait=False, cancel_futures=False)

    def wait_for_idle(self, timeout: float | None) -> bool:
        """제출 작업의 종료를 기다린다. 비동기 호출자는 별도 스레드에서 실행한다."""
        if timeout is not None and (isinstance(timeout, bool) or not math.isfinite(timeout) or timeout < 0):
            raise ValueError('대기 시간은 유한한 0 이상의 숫자여야 합니다.')
        with self._condition:
            return self._condition.wait_for(lambda: not self._pending, timeout)

    @staticmethod
    def _paper_id(file_hash: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f'solo-leveling:local:{file_hash}'))

    def add_local_pdf(self, relative_path: str, request_key: str) -> dict:
        if (not isinstance(request_key, str) or not request_key.strip()
                or len(request_key) > 200):
            from solo_leveling.application.evidence_qa.errors import InvalidArgumentError
            raise InvalidArgumentError('request_key가 필요합니다.')
        # 한 프로세스 안에서는 복사·요청 등록·작업 제출 순서를 고정한다.
        with self.lock:
            if not self._accepting:
                raise IngestionShuttingDownError()
            managed = self.pdf_store.import_pdf(relative_path)
            prepared = prepare_local_ingestion(
                self.db_path, self.chroma_dir, str(managed.path), managed.original_filename,
                self._paper_id(managed.file_hash), request_key=request_key,
                input_fingerprint=managed.input_fingerprint,
            )
            if prepared.registration.disposition == IngestionDisposition.STARTED:
                try:
                    future = self.executor.submit(run_prepared_local_ingestion, prepared,
                        translation_service=self.services.translation,
                        translation_settings=self.services.translation_settings,
                        embedder=self.services.embedder, chroma_client=self.services.chroma_client)
                    self._pending[future] = prepared.registration.job_id
                    future.add_done_callback(self._on_done)
                except Exception:
                    repo.update_job_status(self.db_path, prepared.registration.job_id,
                        JobStatus.FAILED, limitations=['scheduler_failed'])
                    raise
            status = get_ingestion_status(self.db_path, prepared.registration.job_id)
            return {'paper_id': prepared.paper_id, **status}

    def get_status(self, job_id: str) -> dict:
        return get_ingestion_status(self.db_path, job_id)

    def list_jobs(self, *, query=None, limit=20, cursor=None) -> dict:
        from solo_leveling.infrastructure.database.job_queries import list_paper_jobs
        return list_paper_jobs(self.db_path, query=query, limit=limit, cursor=cursor)
