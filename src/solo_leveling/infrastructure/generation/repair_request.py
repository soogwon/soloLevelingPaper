"""대상별 보완 입력과 구절 선택지를 구성한다. 최초 생성·의미 판정과 분리한다."""

from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft


def prepare_repair_request(inputs, evidence, repair):
    data = {}
    data.update(repair)
    by_id = {item['evidence_id']: item for item in inputs}
    contexts = []
    for target in repair['repair_targets']:
        number = target['claim_number']
        if type(number) is not int or not 1 <= number <= len(repair['draft']['claims']):
            raise ValueError('보완 대상 주장 번호가 올바르지 않습니다.')
        left = by_id.get(target['evidence_id'])
        following = by_id.get(target['next_evidence_id'])
        if left is None or (target['next_evidence_id'] is not None
                and (following is None or following['follows_evidence_id'] != left['evidence_id'])):
            raise ValueError('보완 대상 근거 연결이 올바르지 않습니다.')
        # 기존 후보만 재배치한다. 새 원문을 만들거나 근거 ID를 자동으로 보정하지 않는다.
        contexts.append({'claim_number': number, 'reason_code': target['reason_code'],
            'draft_claim': repair['draft']['claims'][number - 1],
            'left': left, 'next': following})
    data['repair_contexts'] = contexts
    from solo_leveling.application.evidence_qa.repair_quotes import repair_quotes
    # 전체 질문을 다시 답하지 않게 대상 초안과 그 근거만 입력에 남긴다.
    from solo_leveling.domain.evidence_qa import GeneratedClaim
    scoped_draft = GeneratedAnswerDraft(tuple(GeneratedClaim(c['text'], tuple(c['evidence_ids']))
        for c in repair['draft']['claims']))
    support_catalog = repair_quotes(evidence, repair['repair_targets'], scoped_draft)
    data['repair_quote_options'] = support_catalog
    allowed_ids = {q['evidence_id'] for option in support_catalog for q in option['quotes']}
    data['evidence'] = [item for item in inputs if item['evidence_id'] in allowed_ids]
    # 원문은 제한된 선택지로만 전송한다. 다른 필드의 전체 청크가 선택지 제한을 우회하지 않게 한다.
    def metadata(item):
        return {key: value for key, value in item.items()
                if key not in ('original_text', 'text_ko')} if item is not None else None
    data['evidence'] = [metadata(item) for item in data['evidence']]
    for context in contexts:
        context['left'] = metadata(context['left'])
        context['next'] = metadata(context['next'])
        context['draft_claim'] = {key: value for key, value in context['draft_claim'].items()
                                  if key != 'supports'}
    data['draft'] = {'claims': [{key: value for key, value in claim.items() if key != 'supports'}
                                for claim in repair['draft']['claims']]}
    data['question'] = '각 보완 대상의 기존 사실과 비교 기준을 유지하고 빠진 조건 또는 원문 연결만 보완하라. 다른 질문에 답하거나 새 주제를 추가하지 마라.'
    return data, support_catalog
