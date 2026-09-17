"""Structural checks only: these do not prove claims or DB version ownership.

Chunk is mutable. Recheck it whenever returning/serializing search-backed results.
The storage adapter must verify parse_revision_id belongs to version_id and that
the selected embedding set uses compatible models and preprocessing.
"""

from solo_leveling.domain.models import Chunk, Evidence
from solo_leveling.domain.evidence_qa import (
    AnswerResult, AnswerStatus, Citation, GetEvidenceResult, SearchResult,
    SearchScope, optional_text, positive_int, require_text,
)


def validate_chunk(chunk: Chunk, scope: SearchScope) -> None:
    require_text(chunk.chunk_id, "chunk_id")
    require_text(chunk.original_text, "original_text")
    positive_int(chunk.pdf_page, "pdf_page")
    optional_text(chunk.text, "text")
    optional_text(chunk.printed_page_label, "printed_page_label")
    optional_text(chunk.section_id, "section_id")
    if chunk.parse_revision_id != scope.parse_revision_id:
        raise ValueError("chunk parse revision is outside scope")
    if chunk.translation_revision_id != scope.translation_revision_id:
        raise ValueError("chunk translation revision is outside scope")
    if scope.translation_revision_id is not None and chunk.text is None:
        raise ValueError("translation search requires translated text")
    if scope.pdf_pages and chunk.pdf_page not in scope.pdf_pages:
        raise ValueError("chunk page is outside scope")
    if scope.section_ids and chunk.section_id not in scope.section_ids:
        raise ValueError("chunk section is outside scope")


def validate_search_result(result: SearchResult) -> None:
    ranks = [item.rank for item in result.items]
    if ranks != sorted(ranks) or len(set(ranks)) != len(ranks):
        raise ValueError("ranks must be sorted and unique")
    for item in result.items:
        validate_chunk(item.chunk, result.scope)


def validate_answer_result(result: AnswerResult) -> None:
    """Check response relationships without asserting citation provenance."""
    needs_claims = result.status in (AnswerStatus.OK, AnswerStatus.PARTIAL)
    needs_reason = result.status in (AnswerStatus.PARTIAL, AnswerStatus.INSUFFICIENT_EVIDENCE)
    if bool(result.claims) != needs_claims or (result.reason_code is not None) != needs_reason:
        raise ValueError("invalid status, claims and reason_code combination")
    claim_ids = [claim.claim_id for claim in result.claims]
    evidence_ids = [citation.evidence_id for citation in result.citations]
    if len(set(claim_ids)) != len(claim_ids) or len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("claim and citation IDs must be unique")
    for claim in result.claims:
        if not set(claim.evidence_ids).issubset(evidence_ids):
            raise ValueError("claim references unknown evidence")


def validate_citation(citation: Citation, search: SearchResult) -> None:
    scope = search.scope
    if (citation.version_id, citation.parse_revision_id, citation.translation_revision_id) != (
        scope.version_id, scope.parse_revision_id, scope.translation_revision_id,
    ):
        raise ValueError("citation revision is outside scope")
    candidates = [item.chunk for item in search.items if item.chunk.chunk_id == citation.chunk_id]
    if not candidates:
        raise ValueError("citation references a chunk outside search results")
    for chunk in candidates:
        validate_chunk(chunk, scope)
        if (citation.pdf_page, citation.printed_page_label) != (chunk.pdf_page, chunk.printed_page_label):
            raise ValueError("citation page differs from chunk")
        if citation.quote_original not in chunk.original_text:
            raise ValueError("original quote is absent from chunk")
        if citation.quote_ko is not None:
            if scope.translation_revision_id is None or chunk.text is None or citation.quote_ko not in chunk.text:
                raise ValueError("translated quote is absent or translation is not pinned")


def validate_answer_against_search(result: AnswerResult, search: SearchResult) -> None:
    validate_search_result(search)
    validate_answer_result(result)
    for citation in result.citations:
        validate_citation(citation, search)


def validate_get_evidence_result(result: GetEvidenceResult, search: SearchResult) -> None:
    """Caller resolves requested IDs against the context; no persistence here."""
    validate_search_result(search)
    ids = [detail.evidence_id for detail in result.evidence]
    if len(set(ids)) != len(ids):
        raise ValueError("evidence IDs must be unique")
    for detail in result.evidence:
        validate_citation(detail, search)


def citation_from_evidence(evidence: Evidence, search: SearchResult) -> Citation:
    """Reuse the existing Evidence model; metadata comes from validated chunks."""
    validate_search_result(search)
    chunks = [item.chunk for item in search.items if item.chunk.chunk_id == evidence.chunk_id]
    if not chunks:
        raise ValueError("evidence chunk is outside search results")
    chunk = chunks[0]
    citation = Citation(
        evidence_id=evidence.evidence_id, chunk_id=chunk.chunk_id,
        version_id=search.scope.version_id, parse_revision_id=chunk.parse_revision_id,
        translation_revision_id=chunk.translation_revision_id,
        printed_page_label=chunk.printed_page_label, pdf_page=chunk.pdf_page,
        quote_ko=evidence.quote_ko, quote_original=evidence.quote_original,
    )
    validate_citation(citation, search)
    return citation
