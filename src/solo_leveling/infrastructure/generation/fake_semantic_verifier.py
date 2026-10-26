"""서버의 판정 처리 테스트용. 의미를 평가하지 않고 지정한 결과를 반환한다."""

from solo_leveling.domain.semantic_verification import ClaimJudgment
from solo_leveling.application.evidence_qa.answer_audit import COVERAGE_CHECKS, REPAIR_CHECKS, answer_quotes


def fixed_audit_checks(claims, *, repair=False, state=None):
    """테스트용 고정 체크리스트이며 실제 의미를 판단하지 않는다."""
    state = state or ('preserved' if repair else 'satisfied')
    quotes = ([answer_quotes(claims[:1])[0]['quote_id']]
              if claims and state in ('satisfied', 'preserved', 'corrected') else [])
    return {name: {'status': state, 'quote_ids': quotes}
            for name in (REPAIR_CHECKS if repair else COVERAGE_CHECKS)}


class FakeSemanticVerifier:
    model = 'fake-semantic'
    prompt_version = 'fixture-v1'

    def __init__(self, outcomes):
        self.outcomes = tuple(outcomes)
        self.calls = []

    def verify(self, claims, evidence):
        self.calls.append((tuple(claims), tuple(evidence)))
        return tuple(ClaimJudgment(c.claim_id, verdict, reason)
                     for c, (verdict, reason) in zip(claims, self.outcomes))

    def verify_answer(self, claims, evidence, question, repair_groups):
        # 기존 테스트용 고정 통과 응답이다. 실제 의미·항목 분해는 수행하지 않는다.
        judgments = self.verify(claims, evidence)
        return judgments, {'coverage': [{'question_part': question, 'status': 'answered',
            'claim_ids': [c.claim_id for c in claims], 'checks': fixed_audit_checks(claims)}],
            'repairs': [{'source_id': g['source_id'], 'status': 'preserved',
                        'checks': fixed_audit_checks([c for c in claims if c.claim_id in g['claim_ids']],
                                                    repair=True, state='preserved' if g['claim_ids'] else 'missing')}
                       for g in repair_groups]}
