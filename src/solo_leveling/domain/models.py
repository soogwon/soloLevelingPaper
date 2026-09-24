"""
domain — 공통 모델·계약

21·22번 문서에서 합의한 필드를 그대로 반영한다.
이 계층은 어떤 인프라(SQLite/Chroma 등)에도 의존하지 않는 순수 데이터 구조다.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStatus(str, Enum):
    """22번 문서: queued/processing/ready/failed/interrupted"""
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class JobStage(str, Enum):
    """10번 문서: download/parse/translate/index"""
    DOWNLOAD = "download"
    PARSE = "parse"
    TRANSLATE = "translate"
    INDEX = "index"


@dataclass
class Paper:
    """최소 논리 단위: 논문"""
    paper_id: str
    title: Optional[str] = None
    source_kind: str = "local_file"  # "local_file" | "url"
    created_at: str = field(default_factory=now_iso)


@dataclass
class PaperVersion:
    """원본 파일 버전. 파일이 바뀌면 새 version_id를 발급한다."""
    version_id: str
    paper_id: str
    file_hash: str
    stored_path: str  # runtime/pdfs 안의 관리 경로
    original_filename: str
    created_at: str = field(default_factory=now_iso)


@dataclass
class ParseRevision:
    """재파싱하면 새 parse_revision_id를 발급한다."""
    parse_revision_id: str
    version_id: str
    page_count: int
    created_at: str = field(default_factory=now_iso)


@dataclass
class TranslationRevision:
    """
    21번 문서: 번역 모델·프롬프트 선택은 B 담당.
    A는 이 리비전과 원문·페이지·작업 상태의 매핑만 연결한다.
    """
    translation_revision_id: str
    parse_revision_id: str
    provider: Optional[str] = None  # 미정 — B가 채움
    model: Optional[str] = None
    created_at: str = field(default_factory=now_iso)


@dataclass
class Chunk:
    """
    22번 문서 텍스트·페이지 합의를 그대로 반영.
    text: 한국어 번역문 (번역 실패 시 원문을 몰래 넣지 않음 — None으로 둠)
    original_text: PDF에서 추출한 원문 (검증·재번역 기준)
    printed_page_label: 확인된 인쇄 번호 문자열("3", "iv" 등). 없거나 불확실하면 None.
    pdf_page: PDF 내 물리 위치, 첫 페이지=1
    """
    chunk_id: str
    parse_revision_id: str
    chunk_index: int  # 논문 내 순번
    original_text: str
    text: Optional[str] = None  # 번역 전에는 None
    translation_revision_id: Optional[str] = None
    printed_page_label: Optional[str] = None
    pdf_page: int = 1
    section_id: Optional[str] = None  # 모르면 None


@dataclass
class EmbeddingSet:
    """모델·차원·전처리 설정 단위. 바뀌면 새 embedding_set_id."""
    embedding_set_id: str
    translation_revision_id: str
    model_name: str
    dimension: int
    created_at: str = field(default_factory=now_iso)


@dataclass
class Evidence:
    """질의응답 근거. citations의 기준 단위."""
    evidence_id: str
    chunk_id: str
    quote_ko: Optional[str] = None
    quote_original: Optional[str] = None


@dataclass
class ProcessingJob:
    job_id: str
    version_id: str
    status: JobStatus = JobStatus.QUEUED
    stage: Optional[JobStage] = None
    limitations: list = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)


@dataclass
class LearningContext:
    """
    10번 문서: context가 원본·파싱·번역 리비전·검수 그래프 버전을 고정한다.
    """
    context_id: str
    version_id: str
    parse_revision_id: str
    translation_revision_id: Optional[str]
    goal: str = "understand"  # understand | implement | skim
    known_concepts: list = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    embedding_set_id: Optional[str] = None  #검색에 사용할 임베딩 색인 ID. DB 저장 시 필수
