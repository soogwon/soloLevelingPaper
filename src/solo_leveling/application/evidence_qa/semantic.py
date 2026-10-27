"""최종 주장만 한 번 검사하고 제공자 출력의 ID·판정 계약을 재검증한다."""

from time import perf_counter
from typing import Protocol, Sequence
from uuid import uuid4
import json
from dataclasses import replace

from solo_leveling.domain.evidence_qa import Claim, EvidenceInput, positive_int
from solo_leveling.domain.semantic_verification import (
    ClaimJudgment, SemanticVerdict, SemanticReason, VerificationFailure, VerificationReport,
    SEMANTIC_REASON_RULES,
    AuditValidationError, VerificationDetail as D,
    RepairPreservation,
)
from solo_leveling.diagnostics import stage, semantic_diagnostic
from .answer_audit import parse_audit, answer_quotes, variable_only_condition
from .original_quotes import original_quotes


class SemanticVerificationError(RuntimeError):
    def __init__(self, code: VerificationFailure, *, detail=None, check=None):
        self.code = code
        self.detail = detail
        self.check = check
        super().__init__(code.value)


class SemanticVerifier(Protocol):
    model: str
    prompt_version: str

    def verify(self, claims: Sequence[Claim], evidence: Sequence[EvidenceInput]) -> tuple[ClaimJudgment, ...]:
        """실제 인용한 근거만 사용하고 새로운 근거나 주장을 생성하지 않는다."""
        ...

    def verify_answer(self, claims, evidence, question, repair_groups):
        """같은 한 번의 호출에서 주장 판정과 질문·보완 검사 원자료를 반환한다."""
        ...


def verification_payload(claims, evidence):
    """각 주장에 허용된 근거를 명시하며 본문은 전송 대상 데이터로만 취급한다."""
    used_ids = {eid for claim in claims for eid in claim.evidence_ids}
    return {
        'original_quotes': original_quotes(claims, evidence),
        'claims': [{'claim_id': c.claim_id, 'text': c.text, 'evidence_ids': list(c.evidence_ids)}
                   for c in claims],
        'evidence': [{'evidence_id': e.evidence_id, 'original_text': e.original_text,
                      'pdf_page': e.pdf_page, 'starts_mid_sentence': e.starts_mid_sentence,
                      'ends_mid_sentence': e.ends_mid_sentence,
                      'follows_evidence_id': e.follows_evidence_id if e.follows_evidence_id in used_ids else None}
                     for e in evidence if e.evidence_id in used_ids],
    }


def validate_judgments(claims, judgments):
    if not isinstance(judgments, tuple) or len(judgments) != len(claims):
        raise SemanticVerificationError(VerificationFailure.INVALID_RESPONSE)
    seen = set()
    for judgment in judgments:
        if (not isinstance(judgment, ClaimJudgment)
                or not isinstance(judgment.claim_id, str)
                or judgment.claim_id in seen
                or not isinstance(judgment.verdict, SemanticVerdict)
                or not isinstance(judgment.reason, SemanticReason)
                or judgment.reason not in SEMANTIC_REASON_RULES[judgment.verdict]):
            raise SemanticVerificationError(VerificationFailure.INVALID_RESPONSE)
        seen.add(judgment.claim_id)
    if seen != {c.claim_id for c in claims}:
        raise SemanticVerificationError(VerificationFailure.INVALID_RESPONSE)


class SemanticVerificationService:
    def __init__(self, verifier: SemanticVerifier, *, max_claims=10, max_input_chars=60000):
        positive_int(max_claims, 'verification_max_claims')
        positive_int(max_input_chars, 'verification_max_input_chars')
        self.verifier = verifier
        self.max_claims = max_claims
        self.max_input_chars = max_input_chars

    def check(self, claims, evidence, *, question=None, repair_groups=()) -> VerificationReport:
        started = perf_counter()
        judgments, failure, audit, detail = (), None, None, None
        detail_check = None
        detail_subreason = None
        # 결과가 없는 보완은 서버가 누락으로 확정한다. 다른 주장으로 보존 여부를 추측시키지 않는다.
        active_groups = tuple(group for group in repair_groups if group['claim_ids'])
        empty_groups = tuple(group for group in repair_groups if not group['claim_ids'])
        try:
            with stage('semantic_verification'):
                data = verification_payload(claims, evidence)
                if question is not None:
                    data.update(question=question, repair_groups=repair_groups, answer_quotes=answer_quotes(claims))
                if (len(claims) > self.max_claims or
                        len(json.dumps(data, ensure_ascii=False))
                        > self.max_input_chars):
                    raise SemanticVerificationError(VerificationFailure.INPUT_LIMIT)
                if question is None:
                    judgments = self.verifier.verify(claims, evidence)
                else:
                    verify_answer = getattr(self.verifier, 'verify_answer', None)
                    if not callable(verify_answer):
                        raise SemanticVerificationError(VerificationFailure.INVALID_RESPONSE)
                    judgments, raw_audit = verify_answer(claims, evidence, question, active_groups)
                    try:
                        audit = parse_audit(raw_audit, question, claims, active_groups)
                        if empty_groups:
                            audit = replace(audit, repairs=audit.repairs + tuple(
                                RepairPreservation(group['source_id'], 'partial') for group in empty_groups))
                    except AuditValidationError as error:
                        detail_check = error.check
                        detail_subreason = error.subreason
                        raise SemanticVerificationError(VerificationFailure.INVALID_RESPONSE, detail=error.detail) from None
                    except (ValueError, TypeError, KeyError):
                        raise SemanticVerificationError(VerificationFailure.INVALID_RESPONSE, detail=D.AUDIT_FORMAT_INVALID) from None
                try:
                    validate_judgments(claims, judgments)
                except SemanticVerificationError:
                    raise SemanticVerificationError(VerificationFailure.INVALID_RESPONSE, detail=D.CLAIM_SET_INVALID) from None
                # 형식 검사를 통과한 모델의 긍정 판정도 변수 이름만 있는 조건이면 낮춘다.
                blocked = {c.claim_id for c in claims
                           if question is not None and variable_only_condition(question, c.text)}
                if blocked:
                    judgments = tuple(replace(j, verdict=SemanticVerdict.PARTIALLY_SUPPORTED,
                        reason=SemanticReason.MISSING_CONDITION)
                        if j.claim_id in blocked and j.verdict == SemanticVerdict.SUPPORTED else j
                        for j in judgments)
                    if audit is not None:
                        audit = replace(audit, detail_codes=tuple(dict.fromkeys(
                            (*audit.detail_codes, D.VARIABLE_ONLY_CONDITION))))
        except SemanticVerificationError as error:
            failure = error.code
            detail_check = error.check or detail_check
            detail = error.detail or (D.CONTRACT_INVALID if failure == VerificationFailure.INVALID_RESPONSE else None)
            judgments = ()
            audit = None
        except (ValueError, TypeError, KeyError, AttributeError):
            # 제공자 계약 오류의 원문은 로그나 사용자 응답에 남기지 않는다.
            failure, judgments, audit = VerificationFailure.INVALID_RESPONSE, (), None
            detail = D.CONTRACT_INVALID
        # 제공자 반환 순서와 무관하게 진단 번호는 입력 주장 순서에 맞춘다.
        if failure is None:
            by_id = {j.claim_id: j for j in judgments}
            judgments = tuple(by_id[c.claim_id] for c in claims)
        report = VerificationReport(uuid4().hex, self.verifier.model, self.verifier.prompt_version,
                                    round((perf_counter() - started) * 1000), judgments, failure, audit, detail, detail_check,
                                    detail_subreason)
        semantic_diagnostic(report)
        return report
