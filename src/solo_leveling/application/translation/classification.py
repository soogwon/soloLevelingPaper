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
    r'(?i)(?:(?:https?://|www\.|doi\.org/)\S+|'
    r'(?:doi\s*:\s*)?10\.\d{4,9}/[-._;()/:A-Z0-9]+|'
    r'arxiv\s*:\s*\d{4}\.\d{4,5}(?:v\d+)?|'
    r'(?:isbn|issn|pmid)\s*:\s*[\dX-]+)')
_NUMBERED_REFERENCE = re.compile(r'^\s*(?:\[\d{1,4}\]|\d{1,4}[.)])\s+')
_AUTHOR = re.compile(r'^[A-Z][A-Za-z\x27-]+,\s+[A-Z]\.')
_YEAR = re.compile(r'(?:^|[\s,(])(?:18|19|20)\d{2}[a-z]?(?:[\s,).]|$)', re.I)
_BIBLIOGRAPHIC_MARKER = re.compile(
    r'(?i)\b(?:vol\.?|volume|no\.?|number|pp?\.?|pages?|proceedings|conference|'
    r'journal|transactions|et\s+al\.)\b')


def classify_translation_source(text: str) -> TranslationStatus | None:
    """번역을 건너뛸 확실한 원문만 분류하고, 일반 본문이면 ``None``을 반환한다."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('source text must be a nonempty string')
    normalized = ' '.join(unicodedata.normalize('NFKC', text).split())
    korean_count = len(_KOREAN.findall(normalized))
    latin_count = sum(len(word) for word in _WORD.findall(normalized))
    # 한국어의 양만으로 영어 설명까지 제외하지 않는다. 혼합 원문은 번역기로
    # 보내며, 아래 기호 비율이나 참고문헌 조건으로도 우회하지 않는다.
    if korean_count:
        return TranslationStatus.TRANSLATION_NOT_REQUIRED if latin_count == 0 else None

    words = _WORD.findall(normalized)
    visible = re.sub(r'\s+', '', normalized)
    alpha_ratio = sum(map(len, words)) / max(1, len(visible))
    # 문장에 포함된 URL이 아니라 원문 전체가 식별자인 경우만 제외한다.
    if _IDENTIFIER.fullmatch(normalized):
        return TranslationStatus.REFERENCE_CONTENT
    # 수식·기호·짧은 식별자처럼 번역할 산문이 없는 구간이다.
    if not words or alpha_ratio < .45:
        return TranslationStatus.TRANSLATION_NOT_REQUIRED

    numbered = _NUMBERED_REFERENCE.match(normalized)
    year = bool(_YEAR.search(normalized))
    marker = bool(_BIBLIOGRAPHIC_MARKER.search(normalized))
    # 번호·저자 표기·연도·서지 표지가 모두 있는 항목만 제외한다.
    # 인식하지 못한 서지 형식은 일반 본문처럼 번역하는 편을 택한다.
    if numbered and _AUTHOR.match(normalized[numbered.end():]) and year and marker:
        return TranslationStatus.REFERENCE_CONTENT
    return None
