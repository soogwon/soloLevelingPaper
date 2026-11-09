"""재파싱 실행의 내부 결과. 공개 MCP 계약과 사용자 동의는 별도다."""
from dataclasses import dataclass
from enum import Enum

from solo_leveling.domain.models import ReparseDisposition


class ReparseFailure(str, Enum):
    FILE_UNAVAILABLE = 'REPARSE_FILE_UNAVAILABLE'
    FILE_MISMATCH = 'REPARSE_FILE_MISMATCH'
    EMPTY_EXTRACTION = 'REPARSE_EMPTY_EXTRACTION'
    INVALID_TRANSLATION = 'REPARSE_INVALID_TRANSLATION'
    TRANSLATION_FAILED = 'REPARSE_TRANSLATION_FAILED'
    PARTIAL_NOT_ALLOWED = 'REPARSE_PARTIAL_NOT_ALLOWED'
    INVALID_EMBEDDING = 'REPARSE_INVALID_EMBEDDING'
    BASE_CHANGED = 'REPARSE_BASE_CHANGED'
    PROVIDER_UNAVAILABLE = 'REPARSE_PROVIDER_UNAVAILABLE'
    CONFIGURATION_CHANGED = 'REPARSE_CONFIGURATION_CHANGED'
    FAILED = 'REPARSE_FAILED'


@dataclass(frozen=True)
class ReparseResult:
    status: ReparseDisposition
    version_id: str
    base_parse_revision_id: str
    base_embedding_set_id: str
    job_id: str | None = None
    new_parse_revision_id: str | None = None
    new_embedding_set_id: str | None = None
    translation_revision_id: str | None = None
    chunk_count: int | None = None
    failed_count: int | None = None
    reason_code: str | None = None
    result_available: bool = False


class ReparseExecutionError(RuntimeError):
    def __init__(self, code='REPARSE_EXECUTION_UNAVAILABLE'):
        super().__init__(code)
