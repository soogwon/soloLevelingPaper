"""청크 원문이 문장 중간에서 시작하거나 끝나는지 판정한다.

청크는 글자 수로 분할되고 앞뒤가 겹치므로 단어·문장 중간에서 잘릴 수 있다.
잘린 조각은 문맥 없이 번역되어 오역되기 쉽다. 여기서는 생성기에 알릴 표시만 만들고
본문을 고치거나 다른 청크와 합치지 않는다. 형식 기반 추정이며 문장 완결성을 보장하지 않는다.
"""

_CLOSERS = ')]}"\'”’'
_SENTENCE_ENDS = ('.', '!', '?')
_LEADING_CONTINUATIONS = ',;:)]}'


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
