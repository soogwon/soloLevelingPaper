"""보완용 원문 선택지. 인접 쌍도 합성 문장 없이 각각의 근거 구절로 보존한다."""

from hashlib import sha256
from .original_quotes import original_quotes
from .evidence_quality import assess_extraction_risk
from solo_leveling.domain.evidence_qa import Claim, ClaimSupport, GeneratedClaim


def repair_quotes(evidence, targets, draft=None):
    if not evidence:
        return []
    quotes = original_quotes((Claim('catalog', '선택지', tuple(e.evidence_id for e in evidence)),), evidence)
    result = []

    def add(selected):
        supports = tuple(ClaimSupport(q['evidence_id'], q['text']) for q in selected)
        claim = GeneratedClaim('경계 확인용 주장', tuple(dict.fromkeys(s.evidence_id for s in supports)), supports)
        # 경계가 확인되지 않은 단독 꼬리·앞조각을 선택지로 제공하지 않는다.
        if assess_extraction_risk(claim, evidence).risky:
            return
        key = ':'.join(q['quote_id'] for q in selected)
        entry = {'selection_id': 'rq_' + sha256(key.encode()).hexdigest()[:24], 'quotes': selected}
        if entry not in result:
            result.append(entry)

    for q in quotes:
        add([q])
    by_id = {e.evidence_id: e for e in evidence}
    for target in targets:
        left = by_id.get(target['evidence_id'])
        right = by_id.get(target['next_evidence_id'])
        if left is None or right is None or right.follows_evidence_id != left.evidence_id:
            continue
        tails = [q for q in quotes if q['evidence_id'] == left.evidence_id and q['end'] == len(left.original_text)]
        heads = [q for q in quotes if q['evidence_id'] == right.evidence_id and q['start'] == 0]
        # 연결 문장 밖의 앞 설명·뒤 설명을 포함하는 전체 청크 선택지는 만들지 않는다.
        for tail in sorted(tails, key=lambda q: -q['start'])[:1]:
            for head in sorted(heads, key=lambda q: q['end'])[:1]:
                add([tail, head])
    if draft is not None:
        scoped = []
        for option in result:
            selected_ids = {q['evidence_id'] for q in option['quotes']}
            numbers = []
            for number, claim in enumerate(draft.claims, 1):
                continuations = [t for t in targets if t['claim_number'] == number
                                 and t['reason_code'] == 'UNFINISHED_TAIL']
                if continuations:
                    # 미완결 꼬리 보완은 연결 쌍만 허용한다. 같은 청크의 단독 문장도 제외한다.
                    if len(option['quotes']) == 2 and any(
                        [q['evidence_id'] for q in option['quotes']] == [t['evidence_id'], t['next_evidence_id']]
                        for t in continuations
                    ):
                        numbers.append(number)
                    continue
                # 원래 인용과 그 대상에 지정된 연결 청크만 보완에 사용할 수 있다.
                allowed = set(claim.evidence_ids)
                for target in targets:
                    if target['claim_number'] == number:
                        allowed.update((target['evidence_id'], target['next_evidence_id']))
                if selected_ids <= allowed:
                    numbers.append(number)
            if numbers:
                scoped.append({**option, 'source_claim_numbers': numbers})
        return scoped
    return result
