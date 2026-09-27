"""한국어 샘플 청크의 연결 검증 전용 생성기. 질문 이해나 사실성 판단을 하지 않는다."""

from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft, GeneratedClaim


class FakeClaimGenerator:
    def generate_claims(self, question, evidence):
        # 모델 호출 없이 전달받은 한국어 본문을 그대로 초안으로 사용한다.
        return GeneratedAnswerDraft(tuple(
            GeneratedClaim(item.text_ko, (item.evidence_id,))
            for item in evidence if item.text_ko is not None
        ))
