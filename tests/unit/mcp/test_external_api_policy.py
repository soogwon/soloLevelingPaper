"""사용자에게 고지한 외부 생성 호출 차단 정책을 실제 어댑터로 확인한다."""

from unittest.mock import Mock

import pytest
import requests

from solo_leveling.application.evidence_qa.ports import GenerationUnavailable
from solo_leveling.infrastructure.generation.openai_generator import (
    OpenAIClaimGenerator, OpenAIGenerationSettings, OpenAISummaryGenerator,
)


@pytest.mark.parametrize(('allow', 'local'), [(False, False), (False, True), (True, True)])
@pytest.mark.parametrize('operation', ['answer', 'repair', 'summary'])
def test_disabled_generation_never_sends_http(monkeypatch, allow, local, operation):
    post = Mock(side_effect=AssertionError('외부 HTTP 요청이 발생하면 안 됩니다.'))
    monkeypatch.setattr(requests, 'post', post)
    settings = OpenAIGenerationSettings('test-only-key', allow_external_api=allow,
                                       local_only=local)
    generator = OpenAIClaimGenerator(settings)
    with pytest.raises(GenerationUnavailable):
        if operation == 'answer':
            generator.generate_claims('테스트 질문', ())
        elif operation == 'repair':
            from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft
            generator.repair_claims('테스트 질문', (), GeneratedAnswerDraft(()), ())
        else:
            OpenAISummaryGenerator(settings).generate_summary(())
    post.assert_not_called()
