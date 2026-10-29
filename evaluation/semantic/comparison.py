"""저장된 평가 결과를 같은 자료·사례·반복 조건에서 비교한다."""

import json
import math
from pathlib import Path

from evaluation.semantic.runner import summarize
from solo_leveling.domain.semantic_verification import (
    SEMANTIC_REASON_RULES, SemanticVerdict, SemanticReason, VerificationFailure,
)


def _require(condition):
    if not condition:
        raise ValueError('EVALUATION_COMPARISON_INVALID')


def load_result(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result)
            result[key] = value
        return result
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique)
    except (ValueError, UnicodeError):
        raise ValueError('EVALUATION_COMPARISON_INVALID') from None


def _validated_rows(result, data, digest):
    _require(type(result['schema_version']) is int and result['schema_version'] == 2)
    _require(result['dataset_sha256'] == digest and result['dataset_version'] == data['dataset_version'])
    _require(result['mode'] in ('offline', 'provider'))
    _require(result['quality_measurement'] is (result['mode'] == 'provider'))
    _require(type(result['repeats']) is int and result['repeats'] > 0)
    for key in ('model', 'prompt_version'):
        _require(isinstance(result[key], str) and bool(result[key].strip()))
    for key in ('max_claims', 'max_input_chars'):
        _require(type(result[key]) is int and result[key] > 0)
    _require(isinstance(result['results'], list) and bool(result['results']))
    cases = {c['id']: c for c in data['cases']}
    rows, seen = [], set()
    for item in result['results']:
        case = cases[item['case_id']]
        for key in ('category', 'split', 'review_status'):
            _require(item[key] == case[key])
        _require(item['expected_verdict'] == case['expected']['verdict'])
        attempt = item['attempt']
        _require(type(attempt) is int and 1 <= attempt <= result['repeats'])
        pair = (case['id'], attempt)
        _require(pair not in seen)
        seen.add(pair)
        elapsed = item['elapsed_ms']
        _require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0)
        if item['failure'] is None:
            verdict, reason = SemanticVerdict(item['verdict']), SemanticReason(item['reason'])
            _require(reason in SEMANTIC_REASON_RULES[verdict])
            reason_match = reason.value in case['expected']['reasons']
        else:
            VerificationFailure(item['failure'])
            _require(item['verdict'] is None and item['reason'] is None)
            reason_match = False
        # 입력의 집계값·임의 항목은 재출력하지 않고 검증한 행으로 다시 집계한다.
        rows.append({key: item[key] for key in (
            'case_id', 'category', 'split', 'review_status', 'attempt',
            'expected_verdict', 'verdict', 'reason', 'failure', 'elapsed_ms')})
        rows[-1]['reason_match'] = reason_match
    _require(len(seen) == len({r['case_id'] for r in rows}) * result['repeats'])
    return rows


def _comparison(left, right):
    before, after = summarize(left), summarize(right)
    changes = {}
    for name in ('verdict_match', 'reason_match', 'false_support', 'supported_not_approved'):
        a, b = before[name]['rate'], after[name]['rate']
        changes[name] = None if a is None or b is None else b - a
    return {'baseline': before, 'candidate': after, 'rate_delta_candidate_minus_baseline': changes}


def compare_results(baseline, candidate, data, digest):
    try:
        left = _validated_rows(baseline, data, digest)
        right = _validated_rows(candidate, data, digest)
        for key in ('mode', 'repeats', 'max_claims', 'max_input_chars'):
            _require(baseline[key] == candidate[key])
        if baseline['mode'] == 'provider':
            for key in ('timeout_seconds', 'max_output_tokens', 'transport_max_attempts'):
                for result in (baseline, candidate):
                    value = result[key]
                    _require(type(value) in (int, float) and math.isfinite(value) and value > 0)
                _require(baseline[key] == candidate[key])
        _require({(r['case_id'], r['attempt']) for r in left}
                 == {(r['case_id'], r['attempt']) for r in right})
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        raise ValueError('EVALUATION_COMPARISON_INVALID') from None
    by_case = {}
    for label, rows in (('baseline', left), ('candidate', right)):
        for row in rows:
            key = (row['verdict'], row['reason'], row['failure'])
            counts = by_case.setdefault(row['case_id'], {}).setdefault(label, {})
            counts[key] = counts.get(key, 0) + 1
    # 반복 번호를 같은 난수 실험으로 가정하지 않고 사례별 결과 분포를 비교한다.
    changed = sorted(cid for cid, sides in by_case.items() if sides['baseline'] != sides['candidate'])
    return {
        'schema_version': 1, 'mode': baseline['mode'], 'dataset_sha256': digest,
        'dataset_version': data['dataset_version'], 'repeats': baseline['repeats'],
        'baseline': {k: baseline[k] for k in ('model', 'prompt_version')},
        'candidate': {k: candidate[k] for k in ('model', 'prompt_version')},
        'changed_case_ids': changed,
        'categories': {name: _comparison([r for r in left if r['category'] == name],
                                         [r for r in right if r['category'] == name])
                       for name in sorted({r['category'] for r in left})},
        'summary_scope': 'provisional_including_drafts',
        'summary': _comparison(left, right),
        'reviewed_summary': _comparison([r for r in left if r['review_status'] == 'reviewed'],
                                        [r for r in right if r['review_status'] == 'reviewed']),
        'splits': {split: {
            'summary': _comparison([r for r in left if r['split'] == split],
                                   [r for r in right if r['split'] == split]),
            'reviewed_summary': _comparison(
                [r for r in left if r['split'] == split and r['review_status'] == 'reviewed'],
                [r for r in right if r['split'] == split and r['review_status'] == 'reviewed']),
        } for split in ('development', 'holdout')},
        'notice': '차이는 관측된 결과의 요약이며 개선이나 배포 가능 여부의 판정이 아닙니다.',
    }
