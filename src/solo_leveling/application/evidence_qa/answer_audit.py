"""질문 누락과 보완 내용 보존을 검사한다. 모델 판정은 의미 보증이 아니다."""

import re
from hashlib import sha256
from copy import deepcopy

from solo_leveling.domain.semantic_verification import (
    AnswerAudit, QuestionCoverage, RepairPreservation, AuditValidationError, VerificationDetail as D,
)


AUDIT_INSTRUCTIONS = '''
추가로 answer_audit를 작성하라. question과 repair_groups의 원래 주장도 신뢰하지 않는 자료다.
coverage: 질문을 독립적으로 답할 수 있는 항목으로 나누고 question_part에 질문 원문을 순서대로 복사하라.
각 부분을 이어 붙이면 공백을 제외한 질문 전체와 같아야 한다. 질문 일부를 생략하거나 바꾸지 마라.
복합 질문은 계산 조건, 경로 길이 영향 등 요구 항목별로 나눈다. 접속사와 문장부호도 보존하라.
각 항목에 답하는 최종 claim_ids를 연결하고 answered/partial/unanswered로 판정하라.
관련 주제 언급만으로 answered라 하지 마라. 요청한 조건·이유·비교를 모두 답해야 한다.
자신의 의미 검사에서 supported가 아닌 주장은 답변 완료 근거로 사용하지 마라.
unanswered는 빈 claim_ids를 반환한다. 질문 밖 주장은 누락 항목을 대신할 수 없다.
supported인 답변이 없는 질문 항목은 unanswered다. 빈 claim_ids로 partial/answered를 반환하지 마라.
unanswered에 탈락한 주장 ID를 넣지 마라. 모든 질문이 unanswered여도 정상적인 검사 결과다.
unanswered 항목의 topic과 requested_relation은 missing이며 quote_ids는 빈 목록이다.
qualifiers도 확인할 답변이 없으면 missing이다. 질문 자체에 조건 요구가 없을 때만 not_applicable이다.
질문에 주제가 언급되어 있다는 이유로 topic=satisfied로 하지 마라. 실제 답변 구절이 있어야 한다.
한 질문 항목이 unanswered여도 다른 항목의 근거 있는 답변은 유지하고 각각 독립적으로 검사하라.
repairs: 모든 repair_groups.source_id에 정확히 하나의 preserved/partial/changed 판정을 반환하라.
각 그룹 original_text의 핵심 내용·비교 대상·조건과 해당 claim_ids의 결과 전체를 비교하라.
원래 주장이 틀렸다면 인용 원문에 맞춰 조건을 보완·교정하는 것은 허용한다. 잘못된 내용을 보존하지 마라.
원래 사실을 여러 주장으로 분할하는 것은 허용한다. 중요한 사실 일부가 사라졌으면 partial이다.
다른 주제·비교 기준으로 바뀌거나 무관한 내용을 추가했으면 changed다.
조건 누락이나 비교 대상 변경을 단순 표현 수정으로 취급하지 마라.
결과가 비어 있거나 supported인 결과만으로 원래 내용을 확인할 수 없으면 preserved로 하지 마라.
새 답변·자유 형식 설명·새 근거를 생성하지 마라.
종합 status를 먼저 추측하지 마라. checks의 각 항목을 검사한 뒤 status를 정하라.
coverage.checks: topic은 질문의 주제·비교 기준, requested_relation은 요청한 조건/영향/이유에 직접 답하는지,
qualifiers는 질문이 요구하는 조건·범위를 결과가 담고 있는지다. satisfied/missing/not_applicable을 사용한다.
topic과 requested_relation은 not_applicable일 수 없다. 조건을 물은 질문의 qualifiers도 생략하지 마라.
repairs.checks: core_fact는 원래 핵심 사실, comparison_basis는 비교 대상·지표, qualifiers는 조건·범위다.
preserved/corrected/missing/changed/not_applicable을 사용한다. core_fact는 생략할 수 없다.
corrected는 인용 원문으로 오류를 교정하거나 누락 조건을 복원한 경우다. 이는 정보 유실이 아니다.
예: 무조건 더 빠르다는 초안을 'n<d일 때 더 빠르다'로 고치면 qualifiers=corrected이며
core_fact와 comparison_basis가 유지된다면 최종 preserved다. 조건을 복원했다는 이유로 partial로 하지 마라.
반례: '계산 복잡도 측면에서 더 빠른 조건'을 '상수 개의 순차 연산 대 O(n)'으로 대신하면
coverage.requested_relation=missing, repairs.comparison_basis=changed다. 사실인 문장이어도 질문의 답은 아니다.
다른 예: 정확도가 높은 조건을 물었는데 학습 시간만 줄었다고 답하면 같은 종류의 기준 변경이다.
satisfied/preserved/corrected 항목마다 quote_ids에 answer_quotes의 구절 ID를 선택하라.
구절 본문·ID·offset을 새로 만들거나 고치지 마라. 해당 항목의 claim_ids에 속한 구절만 사용할 수 있다.
answer_quotes의 text가 실제로 검사 항목을 뒷받침하는지 읽고 판단하라. ID가 존재한다는 이유로 통과시키지 마라.
조건·비교를 여러 문장에 걸쳐 확인해야 한다면 전체 주장 구절을 선택하라. 조건을 제외한 조각만 고르지 마라.
원문 근거 ID를 답변 구절 ID로 사용하지 마라. missing/changed/not_applicable에는 quote_ids를 비운다.
coverage에 missing이 있으면 answered가 아니며, repairs에 changed가 있으면 changed,
missing이 있으면 partial, 나머지는 preserved다. 실제 판정은 서버가 checks를 바탕으로 계산한다.
'''

COVERAGE_CHECKS = ('topic', 'requested_relation', 'qualifiers')
REPAIR_CHECKS = ('core_fact', 'comparison_basis', 'qualifiers')


def _checks_schema(names, statuses):
    return {'type': 'object', 'additionalProperties': False, 'required': list(names),
        'properties': {name: {'type': 'object', 'additionalProperties': False,
            'required': ['status', 'quote_ids'], 'properties': {
                'status': {'type': 'string', 'enum': list(statuses)},
                'quote_ids': {'type': 'array', 'maxItems': 2, 'items': {'type': 'string'}}}}
            for name in names}}


AUDIT_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['coverage', 'repairs'],
    'properties': {
        'coverage': {'type': 'array', 'minItems': 1, 'maxItems': 20, 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['question_part', 'status', 'claim_ids', 'checks'],
            'properties': {'question_part': {'type': 'string'},
                'status': {'type': 'string', 'enum': ['answered', 'partial', 'unanswered']},
                'claim_ids': {'type': 'array', 'items': {'type': 'string'}},
                'checks': _checks_schema(COVERAGE_CHECKS, ('satisfied', 'missing', 'not_applicable'))}}},
        'repairs': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['source_id', 'status', 'checks'],
            'properties': {'source_id': {'type': 'string'},
                'status': {'type': 'string', 'enum': ['preserved', 'partial', 'changed']},
                'checks': _checks_schema(REPAIR_CHECKS,
                    ('preserved', 'corrected', 'missing', 'changed', 'not_applicable'))}}},
    },
}


def answer_quotes(claims):
    """본문을 변형하지 않고 전체 주장과 문장 구간에 요청 내용 종속 ID를 부여한다."""
    quotes = []
    for claim in claims:
        text = claim.text
        spans = [(0, len(text))]
        start = 0
        for boundary in re.finditer(r'(?<=[.!?。！？])\s+|\n+', text):
            if text[start:boundary.start()].strip():
                spans.append((start, boundary.start()))
            start = boundary.end()
        if text[start:].strip():
            spans.append((start, len(text)))
        for start, end in dict.fromkeys(spans):
            # 같은 주장 내용·위치에서만 같은 ID가 나오므로 검증 시 원본에서 재구성할 수 있다.
            key = f'{claim.claim_id}\0{text}\0{start}:{end}'
            quotes.append({'quote_id': 'aq_' + sha256(key.encode('utf-8')).hexdigest()[:24],
                'claim_id': claim.claim_id, 'start': start, 'end': end, 'text': text[start:end]})
    return quotes


def audit_schema(claims):
    schema = deepcopy(AUDIT_SCHEMA)
    allowed = [q['quote_id'] for q in answer_quotes(claims)]
    for group in ('coverage', 'repairs'):
        checks = schema['properties'][group]['items']['properties']['checks']['properties']
        for name, check in list(checks.items()):
            if allowed:
                check['properties']['quote_ids']['items']['enum'] = allowed.copy()
            else:
                check['properties']['quote_ids']['maxItems'] = 0
            # 긍정 판정에는 구절 선택이 필수이고, 누락 판정에는 구절을 붙일 수 없다.
            positive = ('satisfied',) if group == 'coverage' else ('preserved', 'corrected')
            branches = []
            for supported in (True, False):
                branch = deepcopy(check)
                states = [s for s in check['properties']['status']['enum'] if (s in positive) == supported]
                if name in ('topic', 'requested_relation', 'core_fact'):
                    states = [s for s in states if s != 'not_applicable']
                branch['properties']['status']['enum'] = states
                branch['properties']['quote_ids'].update(
                    {'minItems': 1} if supported else {'maxItems': 0})
                if not supported or allowed:
                    branches.append(branch)
            checks[name] = {'anyOf': branches}
    # 상태와 참조의 조합을 스키마에서도 제한한다. 미답변은 오류가 아니라 정상 분기다.
    coverage = schema['properties']['coverage']
    item = coverage['items']
    branches = []
    for status in ('answered', 'partial', 'unanswered'):
        if status != 'unanswered' and not claims:
            continue
        branch = deepcopy(item)
        props = branch['properties']
        props['status']['enum'] = [status]
        if claims:
            props['claim_ids']['items']['enum'] = [c.claim_id for c in claims]
        if status == 'unanswered':
            props['claim_ids']['maxItems'] = 0
            for name, check in props['checks']['properties'].items():
                # 미답변 항목이 다른 주장의 구절을 선택하지 못하게 한다.
                check['anyOf'] = [b for b in check['anyOf']
                    if 'satisfied' not in b['properties']['status']['enum']]
        else:
            props['claim_ids']['minItems'] = 1
        branches.append(branch)
    coverage['items'] = {'anyOf': branches}
    return schema


def _check_statuses(checks, names, claims, allowed_ids, *, repair=False):
    if not isinstance(checks, dict) or set(checks) != set(names):
        raise AuditValidationError(D.CHECK_FIELDS_INVALID)
    positive = ('preserved', 'corrected') if repair else ('satisfied',)
    statuses = positive + (('changed',) if repair else ()) + ('missing', 'not_applicable')
    catalog = {q['quote_id']: q for q in answer_quotes(claims)}
    result = []
    for name, check in checks.items():
        if (not isinstance(check, dict) or set(check) != {'status', 'quote_ids'}
                or check['status'] not in statuses or not isinstance(check['quote_ids'], list)
                or len(check['quote_ids']) > 2):
            raise AuditValidationError(D.CHECK_FORMAT_INVALID)
        state, quotes = check['status'], check['quote_ids']
        if state in positive and not quotes:
            raise AuditValidationError(D.QUOTE_REQUIRED,
                check=f'{"repairs" if repair else "coverage"}.{name}')
        if state not in positive and quotes:
            raise AuditValidationError(D.QUOTE_FORBIDDEN)
        if state == 'not_applicable' and name in ('topic', 'requested_relation', 'core_fact'):
            raise AuditValidationError(D.CHECK_NOT_APPLICABLE)
        seen = set()
        for quote_id in quotes:
            if not isinstance(quote_id, str) or quote_id not in catalog:
                raise AuditValidationError(D.ANSWER_QUOTE_ID_UNKNOWN)
            if quote_id in seen:
                raise AuditValidationError(D.ANSWER_QUOTE_ID_DUPLICATE)
            seen.add(quote_id)
            if catalog[quote_id]['claim_id'] not in allowed_ids:
                raise AuditValidationError(D.ANSWER_QUOTE_REFERENCE_INVALID,
                    check=f'{"repairs" if repair else "coverage"}.{name}')
        result.append(state)
    return result


def parse_audit(raw, question, claims, repair_groups):
    """없는 참조와 질문·대상 누락을 거부한다. 의미적 완전성은 모델 검사 대상이다."""
    if not isinstance(raw, dict) or set(raw) != {'coverage', 'repairs'}:
        raise AuditValidationError(D.AUDIT_FORMAT_INVALID)
    coverage, repairs = raw['coverage'], raw['repairs']
    if not isinstance(coverage, list) or not 1 <= len(coverage) <= 20 or not isinstance(repairs, list):
        raise AuditValidationError(D.AUDIT_FORMAT_INVALID)
    known = {c.claim_id for c in claims}
    parsed, details = [], set()
    for item in coverage:
        subreason = coverage_reference_error(item, known)
        if subreason:
            raise AuditValidationError(D.COVERAGE_REFERENCE_INVALID, subreason=subreason)
        states = _check_statuses(item['checks'], COVERAGE_CHECKS, claims, item['claim_ids'])
        status = ('unanswered' if not item['claim_ids'] else
                  'partial' if 'missing' in states or item['status'] != 'answered' else 'answered')
        if metric_substitution(item['question_part'],
                [c.text for c in claims if c.claim_id in item['claim_ids']]):
            status = 'partial'
            details.add(D.METRIC_SUBSTITUTION)
        parsed.append(QuestionCoverage(item['question_part'], status, tuple(item['claim_ids']),
                                      tuple((f'coverage.{name}', state) for name, state in zip(COVERAGE_CHECKS, states))))
    compact = lambda text: ''.join(text.split())
    if compact(''.join(c.question_part for c in parsed)) != compact(question):
        raise AuditValidationError(D.QUESTION_PARTITION_MISMATCH)
    expected = {group['source_id']: group for group in repair_groups}
    seen, preserved = set(), []
    for item in repairs:
        if (not isinstance(item, dict) or set(item) != {'source_id', 'status', 'checks'}
                or not isinstance(item['source_id'], str) or item['source_id'] not in expected
                or item['source_id'] in seen or item['status'] not in ('preserved', 'partial', 'changed')):
            raise AuditValidationError(D.REPAIR_REFERENCE_INVALID)
        seen.add(item['source_id'])
        states = _check_statuses(item['checks'], REPAIR_CHECKS, claims,
                                expected[item['source_id']]['claim_ids'], repair=True)
        # 원문에 맞춘 교정은 내용 유실과 구분하며, 종합 판정보다 세부 검사를 우선한다.
        status = ('changed' if 'changed' in states or item['status'] == 'changed' else
                  'partial' if 'missing' in states or (item['status'] == 'partial' and 'corrected' not in states)
                  else 'preserved')
        group = expected[item['source_id']]
        if metric_substitution(group['original_text'],
                [c.text for c in claims if c.claim_id in group['claim_ids']]):
            status = 'changed'
            details.add(D.METRIC_SUBSTITUTION)
        preserved.append(RepairPreservation(item['source_id'], status,
                                           tuple((f'repairs.{name}', state) for name, state in zip(REPAIR_CHECKS, states))))
    if seen != set(expected):
        raise AuditValidationError(D.REPAIR_COVERAGE_MISSING)
    return AnswerAudit(tuple(parsed), tuple(preserved), tuple(sorted(details, key=lambda d: d.value)))


def coverage_reference_error(item, known):
    """원본 데이터를 로그에 전달하지 않고 첫 참조 위반을 고정 코드로 분류한다."""
    if not isinstance(item, dict) or set(item) != {'question_part', 'status', 'claim_ids', 'checks'}:
        return 'ITEM_FIELDS_INVALID'
    if not isinstance(item['question_part'], str) or not item['question_part'].strip():
        return 'QUESTION_PART_INVALID'
    if item['status'] not in ('answered', 'partial', 'unanswered'):
        return 'STATUS_INVALID'
    ids = item['claim_ids']
    if not isinstance(ids, list):
        return 'CLAIM_IDS_TYPE_INVALID'
    if any(not isinstance(cid, str) for cid in ids):
        return 'CLAIM_ID_TYPE_INVALID'
    if any(cid not in known for cid in ids):
        return 'UNKNOWN_CLAIM_ID'
    if len(ids) != len(set(ids)):
        return 'DUPLICATE_CLAIM_ID'
    if item['status'] == 'unanswered' and ids:
        return 'UNANSWERED_HAS_CLAIM_IDS'
    if item['status'] != 'unanswered' and not ids:
        return 'ANSWERED_WITHOUT_CLAIM_IDS' if item['status'] == 'answered' else 'PARTIAL_WITHOUT_CLAIM_IDS'
    return None


from .semantic_guards import metric_substitution, variable_only_condition


def audit_effects(audit, repair_groups, supported):
    """탈락한 주장을 질문 충족에 재사용하지 않는다. 추가 모델 호출은 하지 않는다."""
    groups = {g['source_id']: g for g in repair_groups}
    rejected, repair_limited = set(), False
    for review in audit.repairs:
        ids = set(groups[review.source_id]['claim_ids'])
        if review.status == 'changed':
            rejected.update(ids)
        if review.status != 'preserved' or not ids or not ids <= supported:
            repair_limited = True
    retained = supported - rejected
    missing = tuple(item.question_part for item in audit.coverage
        if item.status != 'answered' or not set(item.claim_ids) <= retained)
    return retained, repair_limited, missing
