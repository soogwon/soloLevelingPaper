"""외부 API·DB 없이 기존 검증 서비스와 평가 집계를 확인한다."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from solo_leveling.application.evidence_qa.semantic import SemanticVerificationService
from solo_leveling.domain.evidence_qa import Claim, EvidenceInput
from solo_leveling.domain.semantic_verification import (
    ClaimJudgment, SemanticVerdict as V, SemanticReason as R, SEMANTIC_REASON_RULES,
)


class DatasetError(ValueError):
    """본문을 포함하지 않는 평가 자료 오류."""


def _require(condition):
    if not condition:
        raise DatasetError('EVALUATION_DATASET_INVALID')


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def load_dataset(path):
    raw = Path(path).read_bytes()
    try:
        data = json.loads(raw, object_pairs_hook=_unique_object)
        _require(set(data) == {'schema_version', 'dataset_version', 'source', 'cases'})
        _require(type(data['schema_version']) is int and data['schema_version'] == 1)
        _require(_text(data['dataset_version']) and data['source'] == 'synthetic-authored')
        _require(isinstance(data['cases'], list) and bool(data['cases']))
        ids, groups = set(), {}
        for case in data['cases']:
            _require(set(case) == {'id', 'group', 'split', 'category', 'review_status',
                                  'claim', 'evidence', 'expected'})
            for key in ('id', 'group', 'category'):
                _require(_text(case[key]))
            _require(case['id'] not in ids)
            ids.add(case['id'])
            _require(case['split'] in ('development', 'holdout'))
            _require(case['review_status'] in ('draft', 'reviewed', 'ambiguous'))
            _require(groups.setdefault(case['group'], case['split']) == case['split'])
            expected = case['expected']
            _require(set(expected) == {'verdict', 'reasons', 'rationale'})
            verdict = V(expected['verdict'])
            _require(isinstance(expected['reasons'], list) and bool(expected['reasons']))
            _require(len(set(expected['reasons'])) == len(expected['reasons']))
            _require(all(R(reason) in SEMANTIC_REASON_RULES[verdict] for reason in expected['reasons']))
            _require(_text(expected['rationale']))
            _require(set(case['claim']) == {'text', 'evidence_ids'})
            _require(isinstance(case['claim']['evidence_ids'], list))
            _require(isinstance(case['evidence'], list) and bool(case['evidence']))
            evidence_ids = []
            for e in case['evidence']:
                _require(set(e) == {'id', 'original_text', 'text_ko', 'pdf_page',
                                   'starts_mid_sentence', 'ends_mid_sentence', 'follows_evidence_id'})
                evidence_ids.append(e['id'])
            _require(len(set(evidence_ids)) == len(evidence_ids))
            _require(set(case['claim']['evidence_ids']) == set(evidence_ids))
            claims, evidence = build_inputs(case)
            by_id = {e.evidence_id: e for e in evidence}
            seen = set()
            for e in evidence:
                if e.follows_evidence_id is not None:
                    _require(e.follows_evidence_id in seen)
                    previous = by_id[e.follows_evidence_id]
                    _require(previous.ends_mid_sentence and e.starts_mid_sentence)
                    _require(e.pdf_page in (previous.pdf_page, previous.pdf_page + 1))
                seen.add(e.evidence_id)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise DatasetError('EVALUATION_DATASET_INVALID') from None
    return data, hashlib.sha256(raw).hexdigest()


def build_inputs(case):
    """기대 판정·해설을 전달하지 않고 제품 입력 타입만 구성한다."""
    # 사례·근거 이름의 normal/error 같은 힌트는 모델 입력에 포함하지 않는다.
    ids = {e['id']: f'evidence-{i}' for i, e in enumerate(case['evidence'], 1)}
    claim = Claim('claim-1', case['claim']['text'],
                  tuple(ids[eid] for eid in case['claim']['evidence_ids']))
    evidence = tuple(EvidenceInput(
        evidence_id=ids[e['id']], chunk_id=f'chunk-{i}', text_ko=e['text_ko'],
        original_text=e['original_text'], printed_page_label=None, pdf_page=e['pdf_page'],
        starts_mid_sentence=e['starts_mid_sentence'], ends_mid_sentence=e['ends_mid_sentence'],
        follows_evidence_id=ids[e['follows_evidence_id']] if e['follows_evidence_id'] is not None else None,
    ) for i, e in enumerate(case['evidence'], 1))
    return (claim,), evidence


class OfflineVerifier:
    """정답을 읽지 않고 모든 주장을 지지한다고 반환하는 의도적인 고정 대역."""
    model = 'offline-always-supported'
    prompt_version = 'offline-contract-v1'

    def verify(self, claims, evidence):
        return tuple(ClaimJudgment(c.claim_id, V.SUPPORTED, R.FULL_SUPPORT) for c in claims)


def _rate(numerator, denominator):
    return {'count': numerator, 'denominator': denominator,
            'rate': numerator / denominator if denominator else None}


def summarize(rows):
    # 애매한 기대 정답은 점수에 넣지 않되 실행·장애 집계에는 남긴다.
    eligible = [r for r in rows if r['review_status'] != 'ambiguous']
    completed = [r for r in eligible if r['failure'] is None]
    matrix = {v.value: {w.value: 0 for w in V} for v in V}
    for row in completed:
        matrix[row['expected_verdict']][row['verdict']] += 1
    positives = [r for r in completed if r['expected_verdict'] == V.SUPPORTED.value]
    negatives = [r for r in completed if r['expected_verdict'] != V.SUPPORTED.value]
    by_case = {}
    for row in rows:
        by_case.setdefault(row['case_id'], set()).add((row['verdict'], row['failure']))
    return {
        'attempts': len(rows), 'scored_completed': len(completed),
        'ambiguous_attempts': len(rows) - len(eligible),
        'draft_attempts': sum(r['review_status'] == 'draft' for r in rows),
        'reviewed_attempts': sum(r['review_status'] == 'reviewed' for r in rows),
        'failures': dict(Counter(r['failure'] for r in rows if r['failure'])),
        'confusion_matrix': matrix,
        'verdict_match': _rate(sum(r['verdict'] == r['expected_verdict'] for r in completed), len(completed)),
        'reason_match': _rate(sum(r['reason_match'] for r in completed), len(completed)),
        'false_support': _rate(sum(r['verdict'] == V.SUPPORTED.value for r in negatives), len(negatives)),
        'supported_not_approved': _rate(sum(r['verdict'] != V.SUPPORTED.value for r in positives), len(positives)),
        'undetermined': sum(r['verdict'] == V.UNDETERMINED.value for r in completed),
        'varying_case_ids': sorted(k for k, outcomes in by_case.items() if len(outcomes) > 1),
        'elapsed_ms_total': sum(r['elapsed_ms'] for r in rows),
    }


def evaluate(data, dataset_hash, verifier, *, split='development', repeats=1, max_calls=100,
             max_claims=10, max_input_chars=60000):
    """제공자 실행은 서비스에 맡기고, 기대값 비교는 반환 이후에만 한다."""
    if (split not in ('development', 'holdout', 'all') or type(repeats) is not int
            or repeats < 1 or type(max_calls) is not int or max_calls < 1):
        raise ValueError('EVALUATION_OPTIONS_INVALID')
    cases = [c for c in data['cases'] if split == 'all' or c['split'] == split]
    if not cases:
        raise ValueError('EVALUATION_EMPTY_SELECTION')
    if len(cases) * repeats > max_calls:
        raise ValueError('EVALUATION_CALL_LIMIT')
    service, rows = SemanticVerificationService(
        verifier, max_claims=max_claims, max_input_chars=max_input_chars), []
    for case in cases:
        claims, evidence = build_inputs(case)
        for attempt in range(1, repeats + 1):
            report = service.check(claims, evidence)
            judgment = report.judgments[0] if report.judgments else None
            expected = case['expected']
            rows.append({
                'case_id': case['id'], 'category': case['category'], 'split': case['split'],
                'review_status': case['review_status'], 'attempt': attempt,
                'expected_verdict': expected['verdict'],
                'verdict': judgment.verdict.value if judgment else None,
                'reason': judgment.reason.value if judgment else None,
                'reason_match': judgment.reason.value in expected['reasons'] if judgment else False,
                'failure': report.failure.value if report.failure else None,
                'detail_code': report.detail_code.value if report.detail_code else None,
                'check': report.detail_check, 'rejection_reason': report.detail_subreason,
                'elapsed_ms': report.elapsed_ms,
            })
    return {
        'schema_version': 2, 'dataset_version': data['dataset_version'], 'dataset_sha256': dataset_hash,
        'model': verifier.model, 'prompt_version': verifier.prompt_version,
        'split': split, 'repeats': repeats, 'max_calls': max_calls,
        'max_claims': max_claims, 'max_input_chars': max_input_chars,
        'token_usage': None, 'estimated_cost': None,
        'summary': summarize(rows),
        'summary_scope': 'provisional_including_drafts',
        'reviewed_summary': summarize([r for r in rows if r['review_status'] == 'reviewed']),
        'splits': {name: {
            'summary': summarize([r for r in rows if r['split'] == name]),
            'reviewed_summary': summarize([r for r in rows
                                           if r['split'] == name and r['review_status'] == 'reviewed']),
        } for name in ('development', 'holdout')},
        'categories': {category: summarize([r for r in rows if r['category'] == category])
                       for category in sorted({r['category'] for r in rows})},
        'results': rows,
    }
