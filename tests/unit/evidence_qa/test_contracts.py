"""Run: python -m unittest discover -s tests/unit/evidence_qa -v"""

from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from solo_leveling.domain.models import Chunk, Evidence
from solo_leveling.domain.evidence_qa import (
    AnswerResult, AnswerStatus, Claim, EvidenceDetail, EvidenceInput,
    GeneratedAnswerDraft, GeneratedClaim, GetEvidenceResult, ReasonCode,
    RetrievalMethod, RetrievedChunk, SearchResult, SearchScope,
)
from solo_leveling.application.evidence_qa.ports import ClaimGenerator, ScopedRetriever
from solo_leveling.application.evidence_qa.validators import (
    citation_from_evidence, validate_answer_against_search, validate_answer_result,
    validate_search_result,
)
from solo_leveling.application.evidence_qa.serialization import (
    serialize_answer_result, serialize_get_evidence_result, serialize_search_result,
)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.scope = SearchScope("v1", "p1", "t1", (2,), ("method",))
        self.chunk = Chunk("c1", "p1", 0, "Attention supports parallel computation.",
                           "어텐션은 병렬 계산을 지원한다.", "t1", "iv", 2, "method")
        self.hit = RetrievedChunk(self.chunk, 0.8, 1)
        self.search = SearchResult("어텐션의 특징은?", self.scope, RetrievalMethod.SAMPLE, None, (self.hit,))
        self.evidence = Evidence("e1", "c1", "병렬 계산을 지원한다", "supports parallel computation")
        self.citation = citation_from_evidence(self.evidence, self.search)
        self.claim = Claim("claim1", "어텐션은 병렬 계산을 지원한다.", ("e1",))
        self.answer = AnswerResult(AnswerStatus.OK, self.claim.text, (self.claim,), (self.citation,), None)

    def test_valid_answer_and_json(self):
        payload = serialize_answer_result(self.answer, search=self.search)
        self.assertEqual(json.loads(json.dumps(payload, allow_nan=False)), payload)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["claims"][0]["evidence_ids"], ["e1"])
        self.assertEqual(payload["citations"][0]["pdf_page"], 2)

    def test_search_serialization_responsibilities(self):
        payload = serialize_search_result(self.search)
        self.assertIsNone(payload["embedding_set_id"])
        self.assertEqual(payload["retrieval_method"], "sample")
        self.assertNotIn("embedding_set_id", payload["items"][0])
        self.assertNotIn("score", payload["items"][0]["chunk"])
        json.dumps(payload, allow_nan=False)

    def test_invalid_local_values(self):
        factories = [
            lambda: replace(self.scope, version_id=" "),
            lambda: replace(self.scope, pdf_pages=(0,)),
            lambda: replace(self.scope, pdf_pages=(True,)),
            lambda: replace(self.scope, pdf_pages=(2, 2)),
            lambda: replace(self.scope, section_ids=("",)),
            lambda: replace(self.scope, section_ids=("a", "a")),
            lambda: replace(self.hit, rank=0),
            lambda: replace(self.hit, rank=True),
            lambda: replace(self.hit, score=float("nan")),
            lambda: replace(self.hit, score=float("inf")),
            lambda: replace(self.hit, score=True),
            lambda: replace(self.claim, text=" "),
            lambda: replace(self.claim, evidence_ids=()),
            lambda: replace(self.citation, pdf_page=0),
            lambda: replace(self.citation, quote_original=""),
            lambda: replace(self.search, retrieval_method="sample"),
            lambda: replace(self.search, query=" "),
        ]
        for factory in factories:
            with self.subTest(factory=factory), self.assertRaises(ValueError):
                factory()

    def test_rank_order_and_uniqueness_but_not_contiguity(self):
        for ranks in ((1, 1), (2, 1)):
            result = replace(self.search, items=tuple(replace(self.hit, rank=r) for r in ranks))
            with self.subTest(ranks=ranks), self.assertRaises(ValueError):
                validate_search_result(result)
        validate_search_result(replace(self.search, items=(replace(self.hit, rank=3),)))

    def test_both_scope_filters_and_revisions(self):
        for changes in ({"pdf_page": 3}, {"section_id": "other"},
                        {"parse_revision_id": "p2"}, {"translation_revision_id": "t2"}, {"text": None}):
            result = replace(self.search, items=(replace(self.hit, chunk=replace(self.chunk, **changes)),))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_search_result(result)

    def test_wrong_citation_provenance(self):
        for changes in ({"chunk_id": "missing"}, {"version_id": "v2"},
                        {"parse_revision_id": "p2"}, {"translation_revision_id": "t2"},
                        {"pdf_page": 3}, {"printed_page_label": "v"},
                        {"quote_original": "invented"}, {"quote_ko": "없는 인용"}):
            answer = replace(self.answer, citations=(replace(self.citation, **changes),))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                serialize_answer_result(answer, search=self.search)

    def test_unknown_and_duplicate_ids(self):
        answers = (
            replace(self.answer, claims=(replace(self.claim, evidence_ids=("unknown",)),)),
            replace(self.answer, claims=(self.claim, self.claim)),
            replace(self.answer, citations=(self.citation, self.citation)),
        )
        for answer in answers:
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                validate_answer_result(answer)

    def test_status_matrix(self):
        for status in AnswerStatus:
            for has_claims in (False, True):
                for has_reason in (False, True):
                    result = replace(self.answer, status=status,
                        claims=(self.claim,) if has_claims else (),
                        reason_code=ReasonCode.VERIFICATION_FAILED if has_reason else None)
                    valid = (has_claims == (status in (AnswerStatus.OK, AnswerStatus.PARTIAL))
                             and has_reason == (status in (AnswerStatus.PARTIAL, AnswerStatus.INSUFFICIENT_EVIDENCE)))
                    with self.subTest(status=status, claims=has_claims, reason=has_reason):
                        if valid:
                            validate_answer_result(result)
                        else:
                            with self.assertRaises(ValueError):
                                validate_answer_result(result)

    def test_mutated_chunk_rechecked_before_output(self):
        self.chunk.original_text = "changed"
        with self.assertRaises(ValueError):
            serialize_answer_result(self.answer, search=self.search)
        self.chunk.pdf_page = 0
        with self.assertRaises(ValueError):
            serialize_search_result(self.search)

    def test_original_only_fixture(self):
        scope = replace(self.scope, translation_revision_id=None)
        chunk = replace(self.chunk, text=None, translation_revision_id=None)
        search = replace(self.search, scope=scope, items=(replace(self.hit, chunk=chunk),))
        evidence = replace(self.evidence, quote_ko=None)
        citation = citation_from_evidence(evidence, search)
        self.assertIsNone(citation.quote_ko)
        with self.assertRaises(ValueError):
            citation_from_evidence(self.evidence, search)

    def test_evidence_detail_and_paths(self):
        fields = serialize_answer_result(self.answer, search=self.search)["citations"][0]
        detail = EvidenceDetail(**fields, file_display_name="paper.pdf")
        result = GetEvidenceResult((detail,))
        payload = serialize_get_evidence_result(result, search=self.search)
        self.assertEqual(payload["evidence"][0]["file_display_name"], "paper.pdf")
        self.assertNotIn("file_display_name", fields)
        for path in ("C:\\private\\paper.pdf", "/tmp/paper.pdf", "../paper.pdf", "..", "file:paper.pdf"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                replace(detail, file_display_name=path)
        with self.assertRaises(ValueError):
            serialize_get_evidence_result(GetEvidenceResult((detail, detail)), search=self.search)

    def test_empty_search_and_insufficient_answer(self):
        search = replace(self.search, items=())
        result = AnswerResult(AnswerStatus.INSUFFICIENT_EVIDENCE, "근거가 부족합니다.", (), (), ReasonCode.EVIDENCE_NOT_FOUND)
        self.assertEqual(serialize_answer_result(result, search=search)["claims"], [])

    def test_fake_port_workflow(self):
        search = self.search

        class FakeRetriever:
            def search(self, question: str, scope: SearchScope, top_k: int) -> SearchResult:
                return replace(search, query=question, scope=scope, items=search.items[:top_k])

        class FakeGenerator:
            def generate_claims(self, question, evidence):
                return GeneratedAnswerDraft((GeneratedClaim(evidence[0].text_ko, (evidence[0].evidence_id,)),))

        retriever: ScopedRetriever = FakeRetriever()
        generator: ClaimGenerator = FakeGenerator()
        found = retriever.search("특징은?", self.scope, 1)
        evidence = EvidenceInput("e1", "c1", self.chunk.text, self.chunk.original_text, "iv", 2)
        draft = generator.generate_claims(found.query, (evidence,))
        claim = Claim("server-claim-1", draft.claims[0].text, draft.claims[0].evidence_ids)
        answer = replace(self.answer, claims=(claim,))
        validate_answer_against_search(answer, found)


if __name__ == "__main__":
    unittest.main()
