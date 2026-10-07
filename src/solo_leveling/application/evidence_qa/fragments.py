"""청크 원문이 문장 중간에서 시작하거나 끝나는지 판정한다.

청크는 글자 수로 분할되고 앞뒤가 겹치므로 단어·문장 중간에서 잘릴 수 있다.
잘린 조각은 문맥 없이 번역되어 오역되기 쉽다. 여기서는 생성기에 알릴 표시만 만들고
본문을 고치거나 다른 청크와 합치지 않는다. 형식 기반 추정이며 문장 완결성을 보장하지 않는다.
"""

import re

_CLOSERS = ')]}"\'”’'
_SENTENCE_ENDS = ('.', '!', '?')
_LEADING_CONTINUATIONS = ',;:)]}'


def evidence_body(text: str) -> str:
    """경계 검사 전용: 숫자만 있는 줄을 제외하고 공백을 정규화한다. 저장 원문은 유지한다."""
    return ' '.join(' '.join(line for line in text.splitlines()
                            if not line.strip().isdigit()).split())


def sentence_ends(text: str) -> tuple[int, ...]:
    """소수점과 말줄임표를 제외한 문장 끝 후보다. 언어학적 완결성 검사는 아니다."""
    return tuple(m.end() for m in re.finditer(r'''(?<![.])[.!?](?![.\d])[)\]}"'”’]*(?=\s|$)''', text))


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
    ends_mid = not body.rstrip(_CLOSERS).endswith(_SENTENCE_ENDS)
    return starts_mid, ends_mid
