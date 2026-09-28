"""환경 설정으로 로컬 저장소와 stdio MCP 서버를 준비한다."""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

from solo_leveling.infrastructure.bootstrap import build_services
from solo_leveling.infrastructure.database.repository import mark_interrupted_jobs_on_startup
from solo_leveling.infrastructure.database.schema import init_db
from solo_leveling.infrastructure.generation.openai_generator import OpenAIGenerationSettings
from solo_leveling.infrastructure.storage.vector_store import get_client
from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationConfig
from .server import MCPServices, create_server
from .ingestion import IngestionServices, LocalIngestionManager
from .local_files import LocalPdfStore


@dataclass(frozen=True)
class RuntimePaths:
    data_root: Path
    db_path: Path
    chroma_dir: Path
    import_dir: Path
    pdf_dir: Path


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
    if candidate.is_absolute():
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


def build_runtime_server(values: Mapping[str, str]):
    """저장소를 초기화하고 이전 실행에서 남은 처리 중 작업을 중단 상태로 바꾼다."""
    paths = runtime_paths(values)
    paths.data_root.mkdir(parents=True, exist_ok=True)
    paths.db_path.parent.mkdir(parents=True, exist_ok=True)
    paths.chroma_dir.mkdir(parents=True, exist_ok=True)
    paths.import_dir.mkdir(parents=True, exist_ok=True)
    paths.pdf_dir.mkdir(parents=True, exist_ok=True)
    init_db(str(paths.db_path))
    mark_interrupted_jobs_on_startup(str(paths.db_path))
    services = build_services(str(paths.db_path), get_client(str(paths.chroma_dir)),
        generation=OpenAIGenerationSettings.from_env(values),
        translation=OpenAITranslationConfig.from_env(values))
    try:
        max_bytes = int(values.get('MAX_UPLOAD_BYTES', '31457280'))
        max_workers = int(values.get('MAX_CONCURRENT_INGESTIONS', '1'))
    except ValueError:
        raise ValueError('등록 크기·동시 작업 설정이 올바른 정수여야 합니다.') from None
    if max_workers != 1:
        raise ValueError('현재 MAX_CONCURRENT_INGESTIONS는 1만 지원합니다.')
    ingestion = LocalIngestionManager(str(paths.db_path), str(paths.chroma_dir),
        LocalPdfStore(paths.import_dir, paths.pdf_dir, max_bytes),
        IngestionServices(services.translation, services.translation_settings),
        max_workers=max_workers)
    return create_server(MCPServices(services.answer, services.evidence, ingestion))


def main() -> None:
    """stdout을 MCP 메시지 전용으로 유지한 채 stdio 서버를 실행한다."""
    build_runtime_server(load_environment()).run(transport='stdio')
