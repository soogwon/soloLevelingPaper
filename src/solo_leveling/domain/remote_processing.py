"""#517 내부 처리 자료. 인증·큐·저장·게시 계약이나 공개 wire 모델이 아니다."""

from dataclasses import dataclass

from solo_leveling.domain.models import PaperSection
from solo_leveling.domain.translation import (
    TranslationFailureCode, TranslationSettings, TranslationStatus,
)


class ProcessingError(RuntimeError):
    """상위 실행기가 분류할 고정 내부 오류. 외부 예외 본문을 전달하지 않는다."""


@dataclass(frozen=True, repr=False)
class ProcessingSource:
    owner_user_id: str
    job_id: str
    paper_id: str
    version_id: str
    parse_revision_id: str
    translation_revision_id: str
    embedding_set_id: str
    pdf_sha256: str
    pdf_size: int
    # 원격 worker에서만 사용한다. 로컬/합성 처리 단계는 None을 유지할 수 있다.
    lease_token: str | None = None


@dataclass(frozen=True)
class ProcessingLimits:
    max_pdf_bytes: int
    max_pages: int
    max_chunks: int


@dataclass(frozen=True)
class EmbeddingSpec:
    model_id: str
    model_revision: str
    tokenizer_id: str
    tokenizer_revision: str
    pooling: str
    normalization: str
    max_length: int
    dimension: int
    distance: str
    precision: str
    runtime: str
    runtime_version: str
    artifact_sha256: str


@dataclass(frozen=True, repr=False)
class ProcessingChunk:
    chunk_id: str
    parse_revision_id: str
    chunk_index: int
    original_text: str
    pdf_page: int
    section_id: str | None = None
    printed_page_label: str | None = None


@dataclass(frozen=True)
class PaperFrontMatter:
    """PDF 첫 페이지에서 찾은 서지 정보. 찾지 못한 항목은 None이다.

    지금은 title만 채운다. authors·publication_year는 추출기를 붙일 때 채우며,
    게시 단계는 이미 세 항목을 server_papers의 같은 이름 열로 옮길 수 있다.
    """
    title: str | None = None
    authors: tuple[str, ...] | None = None
    publication_year: int | None = None

    def is_empty(self) -> bool:
        return self.title is None and self.authors is None and self.publication_year is None


FRONT_MATTER_MAX_TEXT = 300
FRONT_MATTER_MAX_AUTHORS = 100


def _front_matter_text(value) -> bool:
    return (type(value) is str and value == value.strip() and 1 <= len(value) <= FRONT_MATTER_MAX_TEXT
            and not any(ord(char) < 32 for char in value))


def front_matter_valid(value) -> bool:
    """None(못 찾음) 또는 server_papers 열에 그대로 넣을 수 있는 값인지 확인한다."""
    if value is None:
        return True
    if type(value) is not PaperFrontMatter or value.is_empty():
        return False
    return ((value.title is None or _front_matter_text(value.title))
            and (value.authors is None or (type(value.authors) is tuple
                                           and 1 <= len(value.authors) <= FRONT_MATTER_MAX_AUTHORS
                                           and all(_front_matter_text(a) for a in value.authors)))
            and (value.publication_year is None
                 or (type(value.publication_year) is int and 1000 <= value.publication_year <= 2100)))


@dataclass(frozen=True, repr=False)
class ParsedDocument:
    page_count: int
    chunks: tuple[ProcessingChunk, ...]
    sections: tuple[PaperSection, ...] = ()
    # 이전 staging payload와 바이트 지문을 맞추려고 None일 때는 직렬화하지 않는다.
    front_matter: PaperFrontMatter | None = None


@dataclass(frozen=True, repr=False)
class ProcessedTranslation:
    chunk_id: str
    text: str | None
    failure_code: TranslationFailureCode | None
    status: TranslationStatus = TranslationStatus.TRANSLATED


@dataclass(frozen=True, repr=False)
class EmbeddingFragment:
    embedding_fragment_id: str
    embedding_set_id: str
    parent_chunk_id: str
    token_start: int
    token_end: int
    parent_token_count: int


@dataclass(frozen=True, repr=False)
class EmbeddingBatch:
    spec: EmbeddingSpec
    chunk_ids: tuple[str, ...]
    vectors: tuple[tuple[float, ...], ...]
    fragments: tuple[EmbeddingFragment, ...] = ()
    split_policy: dict | None = None


@dataclass(frozen=True, repr=False)
class ProcessingResult:
    source: ProcessingSource
    document: ParsedDocument
    translation_settings: TranslationSettings
    translations: tuple[ProcessedTranslation, ...]
    embeddings: EmbeddingBatch | None
    empty_pages: tuple[int, ...]
    # 내부 단계 결과이며 job ready/completed 또는 게시 완료를 뜻하지 않는다.
    status: str
