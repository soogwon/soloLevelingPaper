"""저장 인용문과 PDF 물리 페이지 이동을 구분하는 B 내부 계약(#232)."""
from dataclasses import dataclass, field
from enum import Enum

from .evidence_qa import optional_text, positive_int, require_text


class FileAccessStatus(str, Enum):
    AVAILABLE = 'available'
    MISSING = 'missing'
    DENIED = 'denied'
    EXPIRED = 'expired'
    UNAVAILABLE = 'unavailable'


class NavigationReason(str, Enum):
    ACCESS_NOT_CHECKED = 'access_not_checked'
    FILE_MISSING = 'file_missing'
    ACCESS_DENIED = 'access_denied'
    ACCESS_EXPIRED = 'access_expired'
    ACCESS_UNAVAILABLE = 'access_unavailable'
    VERSION_MISMATCH = 'version_mismatch'
    FILE_HASH_MISMATCH = 'file_hash_mismatch'


@dataclass(frozen=True)
class PdfSourceIdentity:
    """신뢰하는 저장 출처의 내부 정보. 해시와 내부 경로는 UI로 반환하지 않는다."""
    context_id: str
    version_id: str
    parse_revision_id: str
    file_hash: str = field(repr=False)
    page_count: int

    def __post_init__(self):
        for name in ('context_id', 'version_id', 'parse_revision_id', 'file_hash'):
            require_text(getattr(self, name), name)
        positive_int(self.page_count, 'page_count')


@dataclass(frozen=True)
class FileAccessResult:
    """C의 신뢰하는 file-access 경로에서 확인한 정보. 사용자 입력은 증명이 아니다."""
    status: FileAccessStatus
    version_id: str | None = None
    file_hash: str | None = field(default=None, repr=False)
    page_count: int | None = None

    def __post_init__(self):
        if not isinstance(self.status, FileAccessStatus):
            raise ValueError('invalid file access status')
        if self.status is FileAccessStatus.AVAILABLE:
            require_text(self.version_id, 'version_id')
            require_text(self.file_hash, 'file_hash')
            positive_int(self.page_count, 'page_count')
        elif any(value is not None for value in (self.version_id, self.file_hash, self.page_count)):
            raise ValueError('failed access must not include file metadata')


@dataclass(frozen=True)
class PdfNavigationTarget:
    version_id: str
    parse_revision_id: str
    pdf_page: int
    printed_page_label: str | None
    reason_code: NavigationReason | None

    def __post_init__(self):
        require_text(self.version_id, 'version_id')
        require_text(self.parse_revision_id, 'parse_revision_id')
        positive_int(self.pdf_page, 'pdf_page')
        optional_text(self.printed_page_label, 'printed_page_label')
        if self.reason_code is not None and not isinstance(self.reason_code, NavigationReason):
            raise ValueError('invalid navigation reason')

    @property
    def navigation_level(self) -> str:
        return 'page' if self.reason_code is None else 'excerpt_only'

    def to_dict(self) -> dict:
        """내부 해시·파일 경로·접근 URL·원문 본문을 포함하지 않는 출력."""
        return {
            'version_id': self.version_id,
            'parse_revision_id': self.parse_revision_id,
            'pdf_page': self.pdf_page,
            'printed_page_label': self.printed_page_label,
            'navigation_level': self.navigation_level,
            'coordinate_target': None,
            'fallback': 'stored_excerpt',
            'reason_code': self.reason_code.value if self.reason_code else None,
        }
