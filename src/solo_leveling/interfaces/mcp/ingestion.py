"""로컬 PDF 등록을 백그라운드 ingestion 작업에 연결한다."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import threading
import uuid

from solo_leveling.application.translation.service import TranslationService
from solo_leveling.domain.models import IngestionDisposition, JobStatus
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.workers.ingestion import (
    get_ingestion_status, prepare_local_ingestion, run_prepared_local_ingestion,
)
from .local_files import LocalPdfStore


@dataclass(frozen=True)
class IngestionServices:
    translation: TranslationService
    translation_settings: TranslationSettings


class LocalIngestionManager:
    def __init__(self, db_path: str, chroma_dir: str, pdf_store: LocalPdfStore,
                 services: IngestionServices, *, max_workers: int = 1):
        self.db_path = db_path
        self.chroma_dir = chroma_dir
        self.pdf_store = pdf_store
        self.services = services
        self.executor = ThreadPoolExecutor(max_workers=max_workers,
                                           thread_name_prefix='paper-ingestion')
        self.lock = threading.Lock()

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
                        translation_settings=self.services.translation_settings)
                    # 완료 결과는 DB가 기준이며, 예외를 소비해 실행기 경고만 방지한다.
                    future.add_done_callback(lambda completed: completed.exception())
                except Exception:
                    repo.update_job_status(self.db_path, prepared.registration.job_id,
                        JobStatus.FAILED, limitations=['scheduler_failed'])
                    raise
            status = get_ingestion_status(self.db_path, prepared.registration.job_id)
            return {'paper_id': prepared.paper_id, **status}

    def get_status(self, job_id: str) -> dict:
        return get_ingestion_status(self.db_path, job_id)
