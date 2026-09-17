"""Explicit JSON field allowlists. Search provenance must be supplied for answers.

Text fields contain user/document text; this is not a content redaction service.
No database paths, provider secrets or exception objects are serialized.
"""

from solo_leveling.domain.evidence_qa import AnswerResult, Citation, GetEvidenceResult, SearchResult
from .validators import (
    validate_answer_against_search, validate_get_evidence_result, validate_search_result,
)


def _citation(citation: Citation) -> dict:
    return {
        "evidence_id": citation.evidence_id, "chunk_id": citation.chunk_id,
        "version_id": citation.version_id, "parse_revision_id": citation.parse_revision_id,
        "translation_revision_id": citation.translation_revision_id,
        "printed_page_label": citation.printed_page_label, "pdf_page": citation.pdf_page,
        "quote_ko": citation.quote_ko, "quote_original": citation.quote_original,
    }


def serialize_search_result(result: SearchResult) -> dict:
    validate_search_result(result)
    scope = result.scope
    items = []
    for item in result.items:
        chunk = item.chunk
        items.append({
            "chunk": {
                "chunk_id": chunk.chunk_id, "parse_revision_id": chunk.parse_revision_id,
                "chunk_index": chunk.chunk_index, "original_text": chunk.original_text,
                "text": chunk.text, "translation_revision_id": chunk.translation_revision_id,
                "printed_page_label": chunk.printed_page_label,
                "pdf_page": chunk.pdf_page, "section_id": chunk.section_id,
            },
            "score": item.score, "rank": item.rank,
        })
    return {
        "query": result.query,
        "scope": {
            "version_id": scope.version_id, "parse_revision_id": scope.parse_revision_id,
            "translation_revision_id": scope.translation_revision_id,
            "pdf_pages": list(scope.pdf_pages), "section_ids": list(scope.section_ids),
        },
        "retrieval_method": result.retrieval_method.value,
        "embedding_set_id": result.embedding_set_id, "items": items,
    }


def serialize_answer_result(result: AnswerResult, *, search: SearchResult) -> dict:
    validate_answer_against_search(result, search)
    return {
        "status": result.status.value, "answer_ko": result.answer_ko,
        "claims": [{"claim_id": c.claim_id, "text": c.text, "evidence_ids": list(c.evidence_ids)} for c in result.claims],
        "citations": [_citation(c) for c in result.citations],
        "reason_code": result.reason_code.value if result.reason_code is not None else None,
    }


def serialize_get_evidence_result(result: GetEvidenceResult, *, search: SearchResult) -> dict:
    validate_get_evidence_result(result, search)
    return {"evidence": [dict(_citation(d), file_display_name=d.file_display_name) for d in result.evidence]}
