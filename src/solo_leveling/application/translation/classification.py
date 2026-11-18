"""번역 호출 전 원문을 보수적으로 분류한다.

일반 영어 산문을 잘못 건너뛰는 것이 가장 위험하므로, 참고문헌은 명시적인
식별자나 인용 표지가 있을 때만 판정한다. 원문 자체는 로그에 남기지 않는다.
"""

import re
import unicodedata

from solo_leveling.domain.translation import TranslationStatus


_KOREAN = re.compile(r'[\uac00-\ud7a3]')
_WORD = re.compile(r"[A-Za-z]{2,}")
_IDENTIFIER = re.compile(
    r'(?i)(?:https?://|www\.|doi\s*:|doi\.org/|arxiv\s*:|isbn\s*:|issn\s*:|'
    r'pmid\s*:|10\.\d{4,9}/[-._;()/:A-Z0-9]+)')
_NUMBERED_REFERENCE = re.compile(r'^\s*(?:\[\d{1,4}\]|\d{1,4}[.)])\s+')
_YEAR = re.compile(r'(?:^|[\s,(])(?:18|19|20)\d{2}[a-z]?(?:[\s,).]|$)', re.I)
_BIBLIOGRAPHIC_MARKER = re.compile(
    r'(?i)\b(?:vol\.?|volume|no\.?|number|pp?\.?|pages?|proceedings|conference|'
    r'journal|transactions|et\s+al\.)\b')


def classify_translation_source(text: str) -> TranslationStatus | None:
    """번역을 건너뛸 확실한 원문만 분류하고, 일반 본문이면 ``None``을 반환한다."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('source text must be a nonempty string')
    normalized = ' '.join(unicodedata.normalize('NFKC', text).split())
    if _KOREAN.search(normalized):
        return TranslationStatus.TRANSLATION_NOT_REQUIRED

    words = _WORD.findall(normalized)
    visible = re.sub(r'\s+', '', normalized)
    alpha_ratio = sum(map(len, words)) / max(1, len(visible))
    identifier = bool(_IDENTIFIER.search(normalized))
    # URL/DOI가 포함됐다는 이유만으로 일반 본문 전체를 참고문헌으로 보지 않는다.
    # 짧은 식별자 중심 행만 단독 조건으로 인정하고, 긴 행은 아래 서지 표지를 요구한다.
    if identifier and len(words) <= 8:
        return TranslationStatus.REFERENCE_CONTENT
    # 수식·기호·짧은 식별자처럼 번역할 산문이 없는 구간이다.
    if not words or alpha_ratio < .45:
        return TranslationStatus.TRANSLATION_NOT_REQUIRED

    numbered = bool(_NUMBERED_REFERENCE.search(normalized))
    year = bool(_YEAR.search(normalized))
    marker = bool(_BIBLIOGRAPHIC_MARKER.search(normalized))
    # DOI/URL 같은 식별자는 그 자체로 충분하다. 그 밖에는 번호 또는 연도와
    # 학술 서지 표지가 함께 있어야 하므로 일반 본문의 연도를 오분류하지 않는다.
    if (numbered and (year or marker)) or (year and marker) or (identifier and marker):
        return TranslationStatus.REFERENCE_CONTENT
    return None
