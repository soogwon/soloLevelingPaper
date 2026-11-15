"""환경 설정으로 로컬 저장소와 stdio MCP 서버를 준비한다."""

from dataclasses import dataclass
from contextlib import asynccontextmanager
import os
import asyncio
import anyio
import math
from pathlib import Path, PureWindowsPath
from typing import Mapping

from dotenv import dotenv_values

from solo_leveling.infrastructure.bootstrap import build_services
from solo_leveling.infrastructure.embeddings.process_embedder import ProcessEmbedder
from solo_leveling.infrastructure.database.repository import mark_interrupted_jobs_on_startup
from solo_leveling.infrastructure.database.schema import init_db
from solo_leveling.infrastructure.database.dashboard_queries import DashboardReader
from solo_leveling.infrastructure.generation.openai_generator import (
    OpenAIGenerationSettings, OpenAIQuizGenerator,
)
from solo_leveling.infrastructure.generation.semantic_verifier import SemanticSettings
from solo_leveling.infrastructure.storage.vector_store import (
    get_client, close_client, ChromaCloseResult, restore_legacy_index_memberships,
)
from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationConfig
from solo_leveling.application.paper_detail.service import PaperDetailService
from solo_leveling.application.reflection.service import ReflectionService
from solo_leveling.infrastructure.retrieval.paper_detail import SQLitePaperDetailReader
from solo_leveling.application.annotations.service import AnnotationService
from solo_leveling.application.reading_state import ReadingStateService
from solo_leveling.application.quiz.service import QuizService
from solo_leveling.application.server_sync import PaperMetadataSyncService
from solo_leveling.infrastructure.storage.server_client import AuthenticatedServerClient
from .question_card import QuestionKeys
from .server import MCPServices, create_server
from .quiz_tools import QuizContractAdapter, StoredQuizService
from .ingestion import IngestionServices, LocalIngestionManager
from .local_files import LocalPdfStore
from .lifecycle import RequestGate, RuntimeLifetime, report, track_lifetime


@dataclass(frozen=True)
class RuntimePaths:
    data_root: Path
    db_path: Path
    chroma_dir: Path
    import_dir: Path
    pdf_dir: Path


@dataclass(frozen=True)
class RuntimeSettings:
    paths: RuntimePaths
    embedding_timeout: float
    shutdown_timeout: float
    max_bytes: int
    max_workers: int
    generation: OpenAIGenerationSettings
    translation: OpenAITranslationConfig
    verification: SemanticSettings
    server_api_url: str | None


def load_environment(path: Path | None = None,
                     process_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """선택한 env 파일을 읽되 프로세스 환경을 우선하고 전역 환경은 바꾸지 않는다."""
    environment = os.environ if process_env is None else process_env
    configured = path or Path(environment.get('SOLO_LEVELING_ENV_FILE', '.env'))
    values = {}
    if configured.is_file():
        values = {key: value for key, value in
                  dotenv_values(configured, encoding='utf-8-sig', interpolate=False).items()
                  if value is not None}
    values.update(environment)
    return values


def _child(root: Path, value: str, name: str) -> Path:
    candidate = Path(value)
    # 실행 OS와 무관하게 드라이브·UNC·루트 기준 Windows 경로를 거부한다.
    windows_path = PureWindowsPath(value)
    if candidate.is_absolute() or windows_path.drive or windows_path.root:
        raise ValueError(f'{name}은 데이터 루트 아래 상대 경로여야 합니다.')
    resolved = (root / candidate).resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f'{name}이 데이터 루트를 벗어났습니다.')
    return resolved


def runtime_paths(values: Mapping[str, str]) -> RuntimePaths:
    root = Path(values.get('DATA_ROOT', './runtime')).expanduser().resolve()
    db = _child(root, values.get('SQLITE_FILENAME', 'solo_leveling.sqlite3'), 'SQLITE_FILENAME')
    chroma = _child(root, values.get('CHROMA_SUBDIR', 'chroma'), 'CHROMA_SUBDIR')
    imports = _child(root, values.get('IMPORT_SUBDIR', 'imports'), 'IMPORT_SUBDIR')
    pdfs = _child(root, values.get('PDF_SUBDIR', 'pdfs'), 'PDF_SUBDIR')
    return RuntimePaths(root, db, chroma, imports, pdfs)


def validate_runtime_settings(values: Mapping[str, str]) -> RuntimeSettings:
    """저장소 변경·모델 로딩·외부 호출 없이 점검과 실행의 공통 설정을 검증한다."""
    paths = runtime_paths(values)
    try:
        timeout = float(values.get('EMBEDDING_PROCESS_TIMEOUT_SECONDS', '90'))
        shutdown_timeout = float(values.get('MCP_SHUTDOWN_TIMEOUT_SECONDS', '30'))
        max_bytes = int(values.get('MAX_UPLOAD_BYTES', '31457280'))
        max_workers = int(values.get('MAX_CONCURRENT_INGESTIONS', '1'))
    except ValueError:
        raise ValueError('제한 시간·등록 크기·동시 작업 설정이 올바른 숫자여야 합니다.') from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('임베딩 제한 시간은 유한한 양수여야 합니다.')
    if not math.isfinite(shutdown_timeout) or shutdown_timeout < 0:
        raise ValueError('종료 대기 시간은 유한한 0 이상의 숫자여야 합니다.')
    if max_bytes < 1:
        raise ValueError('MAX_UPLOAD_BYTES는 양의 정수여야 합니다.')
    if max_workers != 1:
        raise ValueError('현재 MAX_CONCURRENT_INGESTIONS는 1만 지원합니다.')
    configured_server = values.get('SERVER_API_URL', '').strip()
    server_api_url = (AuthenticatedServerClient.normalize_server_url(configured_server)
                      if configured_server else None)
    return RuntimeSettings(paths, timeout, shutdown_timeout, max_bytes, max_workers,
                           OpenAIGenerationSettings.from_env(values),
                           OpenAITranslationConfig.from_env(values), SemanticSettings.from_env(values),
                           server_api_url)


def _learning_path_service(db_path: str, seed_path: str | None, pdf_root: Path):
    """검수 KG 시드가 잘못돼도 다른 MCP 기능은 그대로 시작한다."""
    from solo_leveling.application.learning_path.service import LearningPathService
    from solo_leveling.knowledge import load_graph
    try:
        return LearningPathService(db_path, load_graph(seed_path or None), pdf_root=pdf_root)
    except Exception:
        report('KG_SEED_UNAVAILABLE')
        return None


def build_runtime_server(values: Mapping[str, str]):
    """설정 검증 후 저장소를 초기화하고 이전 처리 중 작업을 중단 상태로 바꾼다."""
    settings = validate_runtime_settings(values)
    paths = settings.paths
    embedder = ProcessEmbedder(settings.embedding_timeout,
                              log_dir=paths.data_root / 'diagnostics',
                              collect_raw_logs=values.get('SOLO_LEVELING_COLLECT_RAW_LOGS') == '1')
    client = None
    ingestion = None
    recovery = None
    reparse = None
    try:
        paths.data_root.mkdir(parents=True, exist_ok=True)
        paths.db_path.parent.mkdir(parents=True, exist_ok=True)
        paths.chroma_dir.mkdir(parents=True, exist_ok=True)
        paths.import_dir.mkdir(parents=True, exist_ok=True)
        paths.pdf_dir.mkdir(parents=True, exist_ok=True)
        init_db(str(paths.db_path))
        mark_interrupted_jobs_on_startup(str(paths.db_path))
        client = get_client(str(paths.chroma_dir))
        restore_legacy_index_memberships(str(paths.db_path), client)
        services = build_services(str(paths.db_path), client,
            generation=settings.generation,
            translation=settings.translation, embedder=embedder, verification=settings.verification)
        from solo_leveling.diagnostics import semantic_startup_diagnostic
        semantic = services.answer.semantic_verification
        semantic_startup_diagnostic(enabled=settings.verification.enabled,
            connected=semantic is not None,
            prompt_version=semantic.verifier.prompt_version if semantic is not None else None)
        ingestion = LocalIngestionManager(str(paths.db_path), str(paths.chroma_dir),
            LocalPdfStore(paths.import_dir, paths.pdf_dir, settings.max_bytes, data_root=paths.data_root),
            IngestionServices(services.translation, services.translation_settings, embedder, client),
            max_workers=settings.max_workers)

        from solo_leveling.workers.translation_recovery import TranslationRecoveryService
        from .recovery import RecoveryManager
        recovery = RecoveryManager(TranslationRecoveryService(str(paths.db_path), str(paths.chroma_dir),
            translation_service=services.translation, translation_settings=services.translation_settings,
            embedder=embedder, chroma_client=client,
            provider_available=settings.translation.allow_external_api and not settings.translation.local_only))
        from solo_leveling.infrastructure.embeddings.embedder import DEFAULT_MODEL_NAME
        from solo_leveling.workers.reparse import ReparseService
        from .reparse import ReparseManager
        reparse = ReparseManager(ReparseService(str(paths.db_path), str(paths.chroma_dir), paths.pdf_dir,
            translation_service=services.translation, translation_settings=services.translation_settings,
            embedder=embedder, embedding_model=DEFAULT_MODEL_NAME, chroma_client=client,
            provider_available=settings.translation.allow_external_api and not settings.translation.local_only))
        gate = RequestGate()
        lifetime = RuntimeLifetime(gate, ingestion, embedder, client, additional_workers=(recovery, reparse))

        warmup_enabled = values.get('SOLO_LEVELING_EMBEDDING_WARMUP', '1') != '0'

        @asynccontextmanager
        async def lifespan(server):
            try:
                if warmup_enabled:
                    # 첫 질문이 모델 로딩을 기다리지 않도록 서버 시작과 함께 준비한다(#119).
                    from solo_leveling.infrastructure.embeddings.warmup import start_warmup
                    start_warmup(embedder, str(paths.db_path))
                async with anyio.create_task_group() as tasks:
                    if server_sync is not None and values.get('SOLO_LEVELING_SYNC_AUTO_RETRY', '1') != '0':
                        from solo_leveling.application.server_sync.retry import run_auto_retry
                        tasks.start_soon(run_auto_retry, gate.wrap(server_sync))
                    try:
                        yield
                    finally:
                        tasks.cancel_scope.cancel()
            finally:
                lifetime.begin()
                # 취소돼도 종료 스레드는 마지막 사용이 끝날 때까지 소유권을 유지한다.
                with anyio.CancelScope(shield=True):
                    if not await asyncio.to_thread(lifetime.done.wait, settings.shutdown_timeout):
                        report('RUNTIME_SHUTDOWN_DEFERRED')

        annotations = AnnotationService(str(paths.db_path))
        server_sync = None
        pdf_upload = None
        resume = None
        if settings.server_api_url is not None:
            server_client = AuthenticatedServerClient.from_keyring(settings.server_api_url)
            server_sync = PaperMetadataSyncService(str(paths.db_path), server_client)
            import requests
            upload_session = requests.Session()
            upload_session.auth = lambda request: request
            from solo_leveling.application.server_sync import PaperPdfUploadService
            pdf_upload = PaperPdfUploadService(
                str(paths.db_path), paths.pdf_dir, server_client, upload_session)
            from solo_leveling.application.server_sync.resume import ResumeRequestResolver
            resume = ResumeRequestResolver(server_client, str(paths.db_path), paths.pdf_dir, client)
        learning_path = _learning_path_service(str(paths.db_path), values.get('SOLO_LEVELING_KG_SEED'), paths.pdf_dir)
        server = create_server(MCPServices(
            recovery=gate.wrap(recovery), reparse=gate.wrap(reparse),
            question_keys=gate.wrap(QuestionKeys(paths.data_root.resolve() / 'card-question-keys.sqlite3')),
            executions=gate.wrap(services.executions),
            answer=gate.wrap(services.answer), evidence=gate.wrap(services.evidence),
            ingestion=gate.wrap(ingestion), contexts=gate.wrap(services.search.contexts),
            detail=gate.wrap(PaperDetailService(SQLitePaperDetailReader(str(paths.db_path)), annotations)),
            dashboard=gate.wrap(DashboardReader(str(paths.db_path))),
            reflection=gate.wrap(ReflectionService(str(paths.db_path))),
            annotations=gate.wrap(annotations),
            reading_state=gate.wrap(ReadingStateService(str(paths.db_path))),
            quiz=gate.wrap(QuizContractAdapter(StoredQuizService(
                str(paths.db_path), QuizService(
                    str(paths.db_path), OpenAIQuizGenerator(settings.generation))))),
            server_sync=gate.wrap(server_sync) if server_sync is not None else None,
            pdf_upload=gate.wrap(pdf_upload) if pdf_upload is not None else None,
            resume=gate.wrap(resume) if resume is not None else None,
            learning_path=gate.wrap(learning_path) if learning_path is not None else None,
        ), lifespan=lifespan)
        track_lifetime(lifetime)
        return server
    except BaseException:
        if reparse is not None:
            reparse.begin_shutdown()
        if recovery is not None:
            try:
                recovery.begin_shutdown()
            except Exception:
                report('RECOVERY_DRAIN_FAILED')
        if ingestion is not None:
            try:
                ingestion.begin_shutdown()
            except Exception:
                report('RUNTIME_DRAIN_FAILED')
        try:
            embedder.close()
        except Exception:
            report('EMBEDDER_CLOSE_FAILED')
        result = close_client(client)
        if result not in (ChromaCloseResult.CLOSED, ChromaCloseResult.ALREADY_CLOSED):
            report(result.value)
        raise


def main() -> None:
    """stdout을 MCP 메시지 전용으로 유지한 채 stdio 서버를 실행한다."""
    build_runtime_server(load_environment()).run(transport='stdio')
