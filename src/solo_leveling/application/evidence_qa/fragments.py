"""청크 원문이 문장 중간에서 시작하거나 끝나는지 판정한다.

청크는 글자 수로 분할되고 앞뒤가 겹치므로 단어·문장 중간에서 잘릴 수 있다.
잘린 조각은 문맥 없이 번역되어 오역되기 쉽다. 여기서는 생성기에 알릴 표시만 만들고
본문을 고치거나 다른 청크와 합치지 않는다. 형식 기반 추정이며 문장 완결성을 보장하지 않는다.
"""

import re

_LEADING_CONTINUATIONS = ',;:)]}'
_ABBREVIATIONS = re.compile(r'(?i)(?:\b(?:eqs?|figs?|secs?|refs?|dr|prof|mr|mrs|vs|etc)|e\.g|i\.e|et\s+al)\.$')


def evidence_body(text: str) -> str:
    """경계 검사 전용: 숫자만 있는 줄을 제외하고 공백을 정규화한다. 저장 원문은 유지한다."""
    return ' '.join(' '.join(line for line in text.splitlines()
                            if not line.strip().isdigit()).split())


def sentence_ends(text: str, *, outside_page: bool = False) -> tuple[int, ...]:
    """페이지 밖에서는 불확실한 점을 건너뛰어 다음 문장까지 읽지 않는다."""
    ends = []
    for match in re.finditer(r'''[.!?。！？][)\]}"'”’]*''', text):
        pos = match.start()
        if text[pos] == '.':
            before, after = text[:pos], text[pos + 1:]
            if before.endswith('.') or after.startswith('.'):
                if outside_page:
                    break
                continue
            if after[:1].isdigit():  # 1.5, .5
                continue
            abbreviation = _ABBREVIATIONS.search(text[:pos + 1])
            if abbreviation:
                # Eq. 3처럼 번호가 붙은 참조 외에는 약어가 문장 끝일 수도 있다.
                if outside_page and not (re.fullmatch(r'(?:eqs?|figs?|secs?|refs?)\.',
                                                       abbreviation.group(), re.I)
                                         and re.match(r'\s+\d', after)):
                    break
                continue
            if after and after[0].isalnum():
                # PDF 공백 누락으로 확인한 변수 뒤 경계(d.Other claim.)만 허용한다.
                if not (re.search(r'\b[a-z]$', before) and re.match(r'(?:[A-Z][a-z]+\b|[AI](?=\s))', after)):
                    if outside_page:
                        break
                    continue
        ends.append(match.end())
    return tuple(ends)


def incomplete_tail_start(text: str) -> int | None:
    """정규화한 본문에서 마지막 미완결 구간의 시작 위치를 반환한다."""
    ends = sentence_ends(text)
    if ends and ends[-1] == len(text):
        return None
    start = ends[-1] if ends else 0
    while start < len(text) and text[start].isspace():
        start += 1
    return start if text else None


def fragment_flags(original_text: str) -> tuple[bool, bool]:
    """(앞이 잘렸는지, 뒤가 잘렸는지)를 반환한다."""
    lines = original_text.strip().splitlines()
    # 페이지 번호로 보이는 끝의 숫자만 있는 줄은 문장 완결 판단에서 제외한다.
    while len(lines) > 1 and lines[-1].strip().isdigit():
        lines.pop()
    body = '\n'.join(lines).strip()
    if not body:
        return False, False
    starts_mid = body[0].islower() or body[0] in _LEADING_CONTINUATIONS
    ends = sentence_ends(body)
    ends_mid = not ends or ends[-1] != len(body)
    return starts_mid, ends_mid
