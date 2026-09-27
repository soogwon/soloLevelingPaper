"""평가 자료의 구조와 예시 응답만 검사한다. 모델 품질을 평가하지 않는다."""

import json
from pathlib import Path

from solo_leveling.application.evidence_qa.response_parser import parse_generated_answer


def test_generation_cases_are_consistent():
    path = Path(__file__).resolve().parents[1] / 'fixtures/evidence_qa/generation_cases.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['schema_version'] == 1 and data['synthetic'] is True
    cases = data['cases']
    assert len(cases) == 8
    assert len({case['id'] for case in cases}) == len(cases)
    for case in cases:
        assert case['question'].strip()
        assert case['required_points'] and case['forbidden_points']
        ids = [item['evidence_id'] for item in case['evidence']]
        assert len(ids) == len(set(ids))
        assert all(item['text_ko'].strip() for item in case['evidence'])
        allowed = set(case['allowed_evidence_ids'])
        assert allowed <= set(ids)
        draft = parse_generated_answer(json.dumps(case['reference_response']))
        assert case['expected_behavior'] in ('answer', 'abstain')
        assert bool(draft.claims) == (case['expected_behavior'] == 'answer')
        for claim in draft.claims:
            assert claim.evidence_ids
            assert len(claim.evidence_ids) == len(set(claim.evidence_ids))
            assert set(claim.evidence_ids) <= allowed
