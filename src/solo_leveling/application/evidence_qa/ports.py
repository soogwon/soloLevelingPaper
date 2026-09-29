"""Synchronous ports; callers must offload blocking implementations in async hosts."""

from typing import Protocol, Sequence

from solo_leveling.domain.models import Evidence, LearningContext

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

    def get_or_create_learning_context(
        self, version_id: str, goal: str, known_concepts: list[str],
    ) -> LearningContext:
        """게시 완료된 논문 버전에서 같은 학습 설정의 맥락을 준비한다."""
        ...


class ScopedRetriever(Protocol):
    def search(self, question: str, scope: SearchScope, top_k: int) -> SearchResult:
        """Search using a positive top_k and a pre-resolved revision scope."""
        ...


class EvidenceWriter(Protocol):
    def save(self, context_id: str, evidence: Sequence[Evidence]) -> None:
        """검증된 근거 전체를 원자적으로 저장한다. 실패하면 예외를 전달한다."""
        ...


class GenerationUnavailable(RuntimeError):
    """생성 제공자를 사용할 수 없는 경우 어댑터가 전달하는 오류."""


class ClaimGenerator(Protocol):
    def generate_claims(
        self, question: str, evidence: Sequence[EvidenceInput],
    ) -> GeneratedAnswerDraft:
        """Return unverified claims referencing supplied evidence IDs."""
        ...
