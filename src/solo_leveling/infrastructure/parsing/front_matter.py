"""infrastructure/parsing — 첫 페이지 앞부분(front matter)에서 논문 서지 정보 찾기

제목·저자·출판 연도를 찾는다. 셋 모두 확실하지 않으면 None으로 두고 추측하지 않는다.

제목 판정(보수적으로, 확실하지 않으면 None):
- 첫 페이지 위쪽 절반(TITLE_ZONE_RATIO)에 있는 줄만 본다.
- 그중 가장 큰 글자 크기가 본문 크기보다 MIN_TITLE_SIZE_GAP 이상 커야 한다.
- 그 크기의 줄이 위에서부터 연달아 나오는 묶음(최대 MAX_TITLE_LINES줄)을 제목으로 본다.
- 여러 페이지에 반복되는 머리말, URL·arXiv 표기, 글자가 거의 없는 줄은 제외한다.
"""
import re
from typing import Iterable, Optional

from solo_leveling.domain.remote_processing import PaperFrontMatter
from solo_leveling.infrastructure.parsing.sections import LineStyle

TITLE_ZONE_RATIO = 0.5
MIN_TITLE_SIZE_GAP = 1.5
# 같은 제목 줄로 묶을 글자 크기 허용 오차(pt).
TITLE_SIZE_TOLERANCE = 0.6
MAX_TITLE_LINES = 4
MIN_TITLE_LENGTH = 4
MAX_TITLE_LENGTH = 300

_LETTER_RE = re.compile(r'[A-Za-z가-힣À-ɏ]')
_WS_RE = re.compile(r'\s+')
# 제목 끝에 붙는 각주 기호. 숫자는 제목 일부(GPT-4 등)일 수 있어 지우지 않는다.
_TRAILING_MARKS_RE = re.compile(r'[\s*∗†‡§¶,;:]+$')
_NOT_TITLE_RE = re.compile(
    r'^(?:arxiv\s*:|https?://|www\.|doi\s*:|preprint\b|under review\b|published as\b|proceedings\b)',
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    return _WS_RE.sub(' ', text).strip()


def _usable(line: LineStyle, excluded: set[str]) -> bool:
    text = _clean(line.text)
    return (line.top_ratio is not None and line.top_ratio <= TITLE_ZONE_RATIO
            and len(_LETTER_RE.findall(text)) >= 2
            and text.casefold() not in excluded
            and not _NOT_TITLE_RE.match(text))


def detect_title(lines: Iterable[LineStyle], body_size: Optional[float], *,
                 excluded_texts: Iterable[str] = ()) -> Optional[str]:
    """첫 페이지 줄 목록에서 논문 제목을 찾는다. 확실하지 않으면 None."""
    if body_size is None:
        return None
    excluded = {_clean(text).casefold() for text in excluded_texts}
    ordered = sorted((line for line in lines if line.top_ratio is not None), key=lambda line: line.top_ratio)
    candidates = [line for line in ordered if _usable(line, excluded)]
    if not candidates:
        return None
    title_size = max(line.size for line in candidates)
    if title_size < body_size + MIN_TITLE_SIZE_GAP:
        return None
    parts: list[str] = []
    started = False
    for line in ordered:
        is_title_line = (abs(line.size - title_size) <= TITLE_SIZE_TOLERANCE and _usable(line, excluded))
        if is_title_line:
            started = True
            parts.append(_clean(line.text))
            if len(parts) > MAX_TITLE_LINES:
                return None
        elif started:
            break
    title = _TRAILING_MARKS_RE.sub('', ' '.join(parts)).strip()
    if not MIN_TITLE_LENGTH <= len(title) <= MAX_TITLE_LENGTH or len(_LETTER_RE.findall(title)) < 3:
        return None
    return title


# ── 저자 ──────────────────────────────────────────────────────────────
# 저자 판정(제목을 찾은 경우에만):
# - 제목 바로 아래부터 Abstract(또는 첫 페이지 AUTHOR_ZONE_RATIO) 전까지의 줄을 본다.
# - 줄을 큰 간격·쉼표·and로 나눈 조각 중 사람 이름 모양만 남긴다.
#   이메일·숫자·소속 낱말(University, Google, Research ...)이 있는 조각은 버린다.
AUTHOR_ZONE_RATIO = 0.6
MAX_AUTHORS = 50
MAX_NAME_WORDS = 5
_AUTHOR_STOP_RE = re.compile(r'^(?:abstract|초\s*록|요\s*약|summary|keywords?)\b', re.IGNORECASE)
_FOOTNOTE_MARKS_RE = re.compile(r'[∗*†‡§¶♯♮♭⋆★☆✝✉#]+')
_SPLIT_RE = re.compile(r'\s*(?:,|;|\band\b|&|·)\s*', re.IGNORECASE)
_PARTICLES = frozenset({'van', 'von', 'de', 'der', 'den', 'da', 'di', 'del', 'della', 'la', 'le', 'du',
                        'dos', 'das', 'bin', 'ibn', 'al', 'el', 'y', 'ter', 'zu'})
_AFFILIATION_WORDS = frozenset({
    'university', 'universität', 'université', 'universidad', 'institute', 'institut', 'college', 'school',
    'department', 'dept', 'faculty', 'laboratory', 'laboratories', 'lab', 'labs', 'center', 'centre',
    'research', 'brain', 'deepmind', 'google', 'microsoft', 'facebook', 'meta', 'openai', 'amazon',
    'apple', 'nvidia', 'ibm', 'baidu', 'alibaba', 'tencent', 'huawei', 'samsung', 'naver', 'kakao',
    'inc', 'corp', 'corporation', 'ltd', 'llc', 'company', 'ai', 'academy', 'foundation', 'group',
    'team', 'science', 'sciences', 'technology', 'engineering', 'national', 'state', 'city',
    'usa', 'uk', 'china', 'korea', 'japan', 'germany', 'france', 'canada', 'equal', 'contribution',
    'correspondence', 'corresponding', 'author', 'authors', 'work', 'done', 'while', 'intern',
    'conference', 'proceedings', 'workshop', 'abstract', 'preprint', 'review', 'submitted',
})
_HANGUL_NAME_RE = re.compile(r'^[가-힣]{2,5}$')


def _is_name_word(word: str) -> bool:
    if word.casefold() in _PARTICLES:
        return True
    if re.fullmatch(r'[A-Z]\.(?:-?[A-Z]\.)*', word):  # 이니셜: N. / J.-P.
        return True
    if len(word) > 2 and word.isupper():  # 소속 약어·대문자 머리말
        return False
    return word[0].isalpha() and word[0].isupper() and all(c.isalpha() or c in "-'’." for c in word)


def _person_name(fragment: str) -> Optional[str]:
    text = _clean(_FOOTNOTE_MARKS_RE.sub(' ', fragment))
    text = re.sub(r'(?<=[A-Za-z\u00C0-\u024F.])\d+(?:\s*,\s*\d+)*$', '', text).strip(' ,;')
    if not text or '@' in text or any(c.isdigit() for c in text):
        return None
    if _HANGUL_NAME_RE.fullmatch(text):
        return text
    words = text.split()
    if not 2 <= len(words) <= MAX_NAME_WORDS:
        return None
    if any(w.strip('.,').casefold() in _AFFILIATION_WORDS for w in words):
        return None
    if not all(_is_name_word(w) for w in words) or words[-1].casefold() in _PARTICLES:
        return None
    # 이니셜·조사를 뺀 이름 낱말이 4개 이상이면 여러 사람이 한 칸 띄어쓰기로 붙은 것일 수 있다.
    core = [w for w in words if w.casefold() not in _PARTICLES and not re.fullmatch(r'[A-Z]\.(?:-?[A-Z]\.)*', w)]
    if len(core) > 3:
        return None
    return text


def detect_authors(lines: Iterable[LineStyle], title: Optional[str]) -> Optional[tuple[str, ...]]:
    """제목 아래 저자 블록에서 사람 이름만 순서대로 꺼낸다. 하나도 없으면 None."""
    if title is None:
        return None
    ordered = sorted((line for line in lines if line.top_ratio is not None), key=lambda line: line.top_ratio)
    title_words = _clean(title).casefold()
    start = None
    for index, line in enumerate(ordered):
        if _clean(line.text).casefold() and _clean(line.text).casefold() in title_words:
            start = index  # 제목의 마지막 줄 다음부터 본다.
    if start is None:
        return None
    authors: list[str] = []
    for line in ordered[start + 1:]:
        text = _clean(line.text)
        if line.top_ratio > AUTHOR_ZONE_RATIO or _AUTHOR_STOP_RE.match(text):
            break
        for segment in (line.segments or (text,)):
            for fragment in _SPLIT_RE.split(segment):
                name = _person_name(fragment)
                if name is not None and name not in authors:
                    authors.append(name)
    if not authors or len(authors) > MAX_AUTHORS:
        return None
    return tuple(authors)


# ── 출판 연도 ─────────────────────────────────────────────────────────
# 1) 첫 페이지의 학회·저널 표기(예: "31st Conference on ... (NIPS 2017)")의 연도
# 2) 없으면 왼쪽 여백 arXiv 표기의 번호(1706.xxxxx → 2017, 첫 제출 연도)
# 판본 날짜(“2 Aug 2023”)는 개정 시점이라 쓰지 않는다.
_VENUE_RE = re.compile(
    r'\b(?:conference|proceedings|workshop|symposium|journal|transactions|published\s+as|accepted\s+(?:at|to|by)'
    r'|neurips|nips|icml|iclr|aaai|ijcai|acl|emnlp|naacl|coling|eacl|cvpr|iccv|eccv|kdd|sigir|www|tacl|jmlr|tpami)\b',
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r'(?<!\d)(19[89]\d|20\d\d)(?!\d)')
_ARXIV_NEW_RE = re.compile(r'arxiv\s*:\s*(\d{2})(\d{2})\.\d{4,5}', re.IGNORECASE)
_ARXIV_OLD_RE = re.compile(r'arxiv\s*:\s*[a-z\-]+(?:\.[A-Z]{2})?/(\d{2})\d{5}', re.IGNORECASE)


def _arxiv_year(text: str) -> Optional[int]:
    match = _ARXIV_NEW_RE.search(text) or _ARXIV_OLD_RE.search(text)
    if not match:
        return None
    yy = int(match.group(1))
    if match.re is _ARXIV_NEW_RE and not 1 <= int(match.group(2)) <= 12:
        return None
    return 1900 + yy if yy >= 91 else 2000 + yy


# 학회·저널 표기는 첫 페이지 머리말(위 VENUE_EDGE_RATIO)이나 아래 각주 영역에만 있다고 본다.
# 본문 인용 문장의 연도를 출판 연도로 오인하지 않기 위해서다.
VENUE_EDGE_RATIO = 0.15


def detect_publication_year(lines: Iterable[LineStyle], margin_text: str = '') -> Optional[int]:
    """학회·저널 표기 연도를 우선하고, 없으면 arXiv 번호의 첫 제출 연도를 쓴다."""
    lines = [line for line in lines if line.top_ratio is not None]
    arxiv = _arxiv_year(margin_text) or next(
        (year for line in lines if (year := _arxiv_year(line.text)) is not None), None)
    for line in sorted(lines, key=lambda line: line.top_ratio):
        text = _clean(line.text)
        if (VENUE_EDGE_RATIO < line.top_ratio < 1 - VENUE_EDGE_RATIO
                or len(text) > 250 or not _VENUE_RE.search(text)):
            continue
        years = [int(y) for y in _YEAR_RE.findall(text)]
        # arXiv 첫 제출보다 이르거나 너무 늦은 연도는 다른 문맥일 가능성이 커서 버린다.
        years = [y for y in years if arxiv is None or arxiv <= y <= arxiv + 3]
        if years:
            return years[0]
    return arxiv


def detect_front_matter(lines: Iterable[LineStyle], body_size: Optional[float], *,
                        excluded_texts: Iterable[str] = (),
                        margin_text: str = '') -> Optional[PaperFrontMatter]:
    """찾은 서지 정보가 하나도 없으면 None을 돌려준다."""
    lines = list(lines)
    title = detect_title(lines, body_size, excluded_texts=excluded_texts)
    value = PaperFrontMatter(title=title, authors=detect_authors(lines, title),
                             publication_year=detect_publication_year(lines, margin_text))
    return None if value.is_empty() else value
