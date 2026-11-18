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


@dataclass(frozen=True, repr=False)
class ParsedDocument:
    page_count: int
    chunks: tuple[ProcessingChunk, ...]
    sections: tuple[PaperSection, ...] = ()


@dataclass(frozen=True, repr=False)
class ProcessedTranslation:
    chunk_id: str
    text: str | None
    failure_code: TranslationFailureCode | None
    status: TranslationStatus = TranslationStatus.TRANSLATED


@dataclass(frozen=True, repr=False)
class EmbeddingBatch:
    spec: EmbeddingSpec
    chunk_ids: tuple[str, ...]
    vectors: tuple[tuple[float, ...], ...]


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
