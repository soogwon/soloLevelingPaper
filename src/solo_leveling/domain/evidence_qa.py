"""Evidence QA contracts; construction validates values, not factual truth."""

from dataclasses import dataclass
from enum import Enum
import math

from solo_leveling.domain.models import Chunk


def require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def optional_text(value: str | None, name: str) -> None:
    if value is not None:
        require_text(value, name)


def positive_int(value: int, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def require_tuple(values: tuple, item_type: type, name: str) -> None:
    if not isinstance(values, tuple) or any(not isinstance(v, item_type) for v in values):
        raise ValueError(f"{name} must be a tuple of {item_type.__name__}")


class AnswerStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NEEDS_CLARIFICATION = "needs_clarification"


class ReasonCode(str, Enum):
    EVIDENCE_NOT_FOUND = "evidence_not_found"
    EXTRACTION_LIMITED = "extraction_limited"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    UNSUPPORTED_SCOPE = "unsupported_scope"
    VERIFICATION_FAILED = "verification_failed"


class RetrievalMethod(str, Enum):
    SAMPLE = "sample"
    VECTOR = "vector"
    KEYWORD = "keyword"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class SearchScope:
    version_id: str
    parse_revision_id: str
    translation_revision_id: str | None
    pdf_pages: tuple[int, ...] = ()
    section_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_text(self.version_id, "version_id")
        require_text(self.parse_revision_id, "parse_revision_id")
        optional_text(self.translation_revision_id, "translation_revision_id")
        require_tuple(self.pdf_pages, int, "pdf_pages")
        require_tuple(self.section_ids, str, "section_ids")
        for page in self.pdf_pages:
            positive_int(page, "pdf_page")
        for section in self.section_ids:
            require_text(section, "section_id")
        if len(set(self.pdf_pages)) != len(self.pdf_pages) or len(set(self.section_ids)) != len(self.section_ids):
            raise ValueError("scope filters must not contain duplicates")


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    score: float
    rank: int

    def __post_init__(self) -> None:
        if not isinstance(self.chunk, Chunk):
            raise ValueError("chunk must be a Chunk")
        if type(self.score) not in (int, float) or not math.isfinite(self.score):
            raise ValueError("score must be finite")
        positive_int(self.rank, "rank")


@dataclass(frozen=True)
class SearchResult:
    query: str
    scope: SearchScope
    retrieval_method: RetrievalMethod
    embedding_set_id: str | None
    items: tuple[RetrievedChunk, ...]

    def __post_init__(self) -> None:
        require_text(self.query, "query")
        if not isinstance(self.scope, SearchScope) or not isinstance(self.retrieval_method, RetrievalMethod):
            raise ValueError("invalid scope or retrieval method")
        optional_text(self.embedding_set_id, "embedding_set_id")
        require_tuple(self.items, RetrievedChunk, "items")


@dataclass(frozen=True)
class GeneratedClaim:
    text: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        require_text(self.text, "text")
        require_tuple(self.evidence_ids, str, "evidence_ids")
        for evidence_id in self.evidence_ids:
            require_text(evidence_id, "evidence_id")


@dataclass(frozen=True)
class Claim:
    claim_id: str
    text: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        require_text(self.claim_id, "claim_id")
        GeneratedClaim(self.text, self.evidence_ids)
        if not self.evidence_ids:
            raise ValueError("final claims require evidence")


@dataclass(frozen=True)
class Citation:
    evidence_id: str
    chunk_id: str
    version_id: str
    parse_revision_id: str
    translation_revision_id: str | None
    printed_page_label: str | None
    pdf_page: int
    quote_ko: str | None
    quote_original: str

    def __post_init__(self) -> None:
        for name in ("evidence_id", "chunk_id", "version_id", "parse_revision_id", "quote_original"):
            require_text(getattr(self, name), name)
        for name in ("translation_revision_id", "printed_page_label", "quote_ko"):
            optional_text(getattr(self, name), name)
        positive_int(self.pdf_page, "pdf_page")


@dataclass(frozen=True)
class EvidenceDetail(Citation):
    file_display_name: str

    def __post_init__(self) -> None:
        super().__post_init__()
        require_text(self.file_display_name, "file_display_name")
        if self.file_display_name in (".", "..") or any(c in self.file_display_name for c in '/\\:\x00\r\n'):
            raise ValueError("file_display_name must be a file name, not a path")


@dataclass(frozen=True)
class GetEvidenceResult:
    evidence: tuple[EvidenceDetail, ...]

    def __post_init__(self) -> None:
        require_tuple(self.evidence, EvidenceDetail, "evidence")


@dataclass(frozen=True)
class AnswerResult:
    status: AnswerStatus
    answer_ko: str
    claims: tuple[Claim, ...]
    citations: tuple[Citation, ...]
    reason_code: ReasonCode | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, AnswerStatus):
            raise ValueError("status must be AnswerStatus")
        if self.reason_code is not None and not isinstance(self.reason_code, ReasonCode):
            raise ValueError("reason_code must be ReasonCode or None")
        require_text(self.answer_ko, "answer_ko")
        require_tuple(self.claims, Claim, "claims")
        require_tuple(self.citations, Citation, "citations")


@dataclass(frozen=True)
class EvidenceInput:
    evidence_id: str
    chunk_id: str
    text_ko: str | None
    original_text: str
    printed_page_label: str | None
    pdf_page: int

    def __post_init__(self) -> None:
        for name in ("evidence_id", "chunk_id", "original_text"):
            require_text(getattr(self, name), name)
        optional_text(self.text_ko, "text_ko")
        optional_text(self.printed_page_label, "printed_page_label")
        positive_int(self.pdf_page, "pdf_page")


@dataclass(frozen=True)
class GeneratedAnswerDraft:
    claims: tuple[GeneratedClaim, ...]

    def __post_init__(self) -> None:
        require_tuple(self.claims, GeneratedClaim, "claims")
