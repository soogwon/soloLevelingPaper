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
_REFERENCE_SECTION = re.compile(
    r'(?i)(?:^|[-_\s])(?:references?|bibliography|literature[-_\s]+cited|'
    r'참고[-_\s]*문헌)(?:$|[-_\s])')
_REFERENCE_BOUNDARY = re.compile(
    r'(?i)\b(?:appendix|supplement(?:ary)?(?:\s+material)?|acknowledg(?:e)?ments?)\b')
_INLINE_AUTHOR = re.compile(
    r"\b[A-Z][A-Za-z'’-]+(?:,\s*(?:[A-Z]\.){1,3}|\s+(?:and|&)\s+[A-Z][A-Za-z'’-]+)")
_VENUE_MARKER = re.compile(
    r'(?i)\b(?:arxiv|neurips|nips|iclr|icml|eccv|cvpr|iccv|acl|emnlp|naacl|aaai)\b')


def _reference_section_with_multiple_citations(text: str, section_id: str | None) -> bool:
    """참고문헌 섹션의 복수 인용 묶음만 판정한다.

    섹션 이름 하나만으로 본문을 건너뛰지 않는다. PDF의 2단 추출처럼 여러
    참고문헌이 한 청크에 섞인 경우를 위해 연도와 서지 표식의 밀도를 함께 본다.
    부록·보충자료 경계가 들어온 청크는 참고문헌 꼬리와 본문이 섞였을 수 있어
    항상 번역기로 보낸다.
    """
    if not isinstance(section_id, str) or not _REFERENCE_SECTION.search(section_id.strip()):
        return False
    if _REFERENCE_BOUNDARY.search(text):
        return False
    years = len(_YEAR.findall(text))
    numbered = len(re.findall(r'(?m)^\s*(?:\[\d{1,4}\]|\d{1,4}[.)])\s+', text))
    identifiers = len(_IDENTIFIER.findall(text))
    authors = len(_INLINE_AUTHOR.findall(text))
    markers = len(_BIBLIOGRAPHIC_MARKER.findall(text))
    venues = len(_VENUE_MARKER.findall(text))
    # 최소 두 개의 연도와 두 개의 독립적인 서지 표식이 있어야 한다. 한 문장에
    # 연도나 URL이 우연히 들어간 경우에는 참고문헌 섹션 안에서도 번역을 유지한다.
    citation_signals = numbered + identifiers + authors + markers + venues
    return years >= 2 and citation_signals >= 2


def classify_translation_source(
    text: str, *, section_id: str | None = None,
) -> TranslationStatus | None:
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
    if _reference_section_with_multiple_citations(normalized, section_id):
        return TranslationStatus.REFERENCE_CONTENT
    return None
