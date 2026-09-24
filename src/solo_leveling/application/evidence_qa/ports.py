"""Synchronous ports; callers must offload blocking implementations in async hosts."""

from typing import Protocol, Sequence

from solo_leveling.domain.models import LearningContext

from solo_leveling.domain.evidence_qa import (
    EvidenceInput, GeneratedAnswerDraft, SearchResult, SearchScope,
)


class LearningContextReader(Protocol):
    def get_context(self, context_id: str) -> LearningContext | None:
        """저장된 학습 맥락을 조회한다. 없으면 None을 반환한다."""
        ...


class DefaultContextStore(LearningContextReader, Protocol):
    def get_or_create_default_context(self, version_id: str) -> LearningContext:
        """게시 완료된 논문 버전의 기본 맥락을 준비한다."""
        ...


class ScopedRetriever(Protocol):
    def search(self, question: str, scope: SearchScope, top_k: int) -> SearchResult:
        """Search using a positive top_k and a pre-resolved revision scope."""
        ...


class ClaimGenerator(Protocol):
    def generate_claims(
        self, question: str, evidence: Sequence[EvidenceInput],
    ) -> GeneratedAnswerDraft:
        """Return unverified claims referencing supplied evidence IDs."""
        ...
