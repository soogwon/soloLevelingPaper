"""근거 연결형 논문 요약 생성(M1-05, #50).

번역된 청크를 근거로 묶어 생성기에 넘기고, 근거를 확인할 수 있는 항목만 저장한다.
생성기가 만든 항목은 신뢰하지 않는다. 형식이 틀렸거나 넘기지 않은 근거를 인용한 항목은 버린다.
"""
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence
from uuid import uuid4

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.domain.evidence_qa import EvidenceInput
from solo_leveling.domain.models import Chunk, Evidence
from solo_leveling.domain.paper_summary import (
    PAPER_SCOPE, PaperSummary, SummaryItem, SummaryValidationError, split_supported,
)
from solo_leveling.infrastructure.database import repository as repo

MAX_CHUNKS_PER_CALL = 20


@dataclass(frozen=True)
class DraftItem:
    """생성기가 돌려주는 검증 전 항목."""
    kind: str
    text: str
    evidence_ids: tuple


class SummaryGenerator(Protocol):
    def generate_summary(self, evidence: Sequence[EvidenceInput]) -> Sequence[DraftItem]:
        """주어진 근거만으로 요약 항목 초안을 만든다. 검증은 서비스가 한다."""
        ...


class NoSupportedSummary(Exception):
    """근거로 확인되는 요약 항목이 하나도 없어 아무것도 저장하지 않았다."""


@dataclass(frozen=True)
class SummaryResult:
    summary: PaperSummary
    dropped_count: int  # 형식 오류이거나 근거를 확인할 수 없어 버린 항목 수


class SummaryService:
    def __init__(self, db_path: str, generator: SummaryGenerator,
                 max_chunks_per_call: int = MAX_CHUNKS_PER_CALL,
                 id_factory: Callable[[], str] = lambda: str(uuid4())):
        if max_chunks_per_call < 1:
            raise ValueError('max_chunks_per_call must be positive')
        self.db_path = db_path
        self.generator = generator
        self.max_chunks = max_chunks_per_call
        self.new_id = id_factory

    def generate(self, context_id: str, scope: str = PAPER_SCOPE) -> SummaryResult:
        context = repo.get_learning_context(self.db_path, context_id)
        if context is None:
            raise ResourceNotFoundError('context not found')
        chunks = []
        if context.translation_revision_id:
            chunks = repo.get_translated_chunks(
                self.db_path, context.parse_revision_id, context.translation_revision_id)
        if scope != PAPER_SCOPE:
            chunks = [c for c in chunks if c.section_id == scope]
        if not chunks:
            raise SummaryValidationError('요약할 번역된 본문이 없습니다.')

        kept, evidences, dropped = [], {}, 0
        for batch in self._batches(chunks):
            inputs, records = [], {}
            for chunk in batch:
                evidence_id = self.new_id()
                inputs.append(EvidenceInput(evidence_id, chunk.chunk_id, chunk.text, chunk.original_text,
                                            chunk.printed_page_label, chunk.pdf_page))
                records[evidence_id] = Evidence(evidence_id, chunk.chunk_id, chunk.text, chunk.original_text)
            valid = []
            for draft in self.generator.generate_summary(inputs):
                try:
                    valid.append(SummaryItem(draft.kind, draft.text, tuple(draft.evidence_ids)))
                except (SummaryValidationError, TypeError):
                    dropped += 1
            supported, unsupported = split_supported(valid, records)
            dropped += len(unsupported)
            kept.extend(supported)
            for item in supported:
                for evidence_id in item.evidence_ids:
                    evidences[evidence_id] = records[evidence_id]
        if not kept:
            raise NoSupportedSummary('근거로 확인되는 요약 항목이 없습니다.')
        repo.save_evidences(self.db_path, context_id, list(evidences.values()))
        summary = repo.save_summary(self.db_path, context_id, scope, kept)
        return SummaryResult(summary, dropped)

    def _batches(self, chunks: Sequence[Chunk]):
        """같은 섹션끼리 묶고, 한 번의 호출이 max_chunks를 넘지 않게 나눈다."""
        group, section = [], None
        for chunk in chunks:
            if group and chunk.section_id != section:
                yield from self._split(group)
                group = []
            section = chunk.section_id
            group.append(chunk)
        if group:
            yield from self._split(group)

    def _split(self, group):
        for start in range(0, len(group), self.max_chunks):
            yield group[start:start + self.max_chunks]
