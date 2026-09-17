"""Synchronous ports; callers must offload blocking implementations in async hosts."""

from typing import Protocol, Sequence

from solo_leveling.domain.evidence_qa import (
    EvidenceInput, GeneratedAnswerDraft, SearchResult, SearchScope,
)


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
