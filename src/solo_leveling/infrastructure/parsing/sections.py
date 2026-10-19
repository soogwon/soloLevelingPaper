"""infrastructure/parsing — 논문 섹션 제목 인식 (M1-05a, #65)

글자 크기·굵기와 제목 모양으로 큰 제목(Abstract, 1 Introduction, 2 Method ...)만 찾는다.
세부 제목(3.1 등)은 다루지 않는다. 확실하지 않으면 제목으로 보지 않는다. 못 찾은 구간은
section_id가 None으로 남고, 그래도 논문 등록은 실패하지 않는다.
"""
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Optional

# 본문보다 이만큼 이상 크면 제목 후보로 본다(pt).
MIN_SIZE_GAP = 0.8
MAX_HEADING_LENGTH = 80
MAX_HEADING_WORDS = 12


@dataclass(frozen=True)
class PageHeading:
    """페이지 텍스트(page.text) 안에서 섹션 제목이 시작하는 글자 위치."""
    title: str
    offset: int


@dataclass(frozen=True)
class LineStyle:
    """페이지 텍스트의 한 줄과 그 줄의 글자 크기·굵기."""
    text: str
    size: float
    bold: bool


# 번호 없는 제목으로 인정하는 표준 이름. 소문자로 비교한다.
STANDARD_HEADINGS = frozenset({
    'abstract', 'introduction', 'related work', 'related works', 'background',
    'preliminaries', 'method', 'methods', 'methodology', 'approach', 'model',
    'experiments', 'experiment', 'evaluation', 'results', 'discussion',
    'conclusion', 'conclusions', 'limitations', 'future work', 'references',
    'acknowledgments', 'acknowledgements', 'appendix',
    '초록', '요약', '서론', '서 론', '관련 연구', '배경', '방법', '방법론', '실험',
    '결과', '논의', '결론', '결 론', '참고문헌', '참고 문헌', '감사의 글',
})

# 큰 제목의 번호: "1 Introduction", "1. Introduction", "II. Related Work".
# "3.1 Encoder"처럼 세부 번호가 붙은 것은 맞지 않는다.
_NUMBERED_RE = re.compile(r'^(?:\d{1,2}|[IVX]{1,4})\s*[.)]?\s+(\S.*)$')
_LETTER_RE = re.compile(r'[A-Za-z가-힣]')


def _clean(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


def _title_part(text: str) -> Optional[str]:
    """번호를 뗀 제목 부분. 번호가 없으면 줄 전체."""
    match = _NUMBERED_RE.match(text)
    return match.group(1).strip() if match else None


def _looks_like_title(title: str) -> bool:
    if len(_LETTER_RE.findall(title)) < 2:
        return False
    if len(title.split()) > MAX_HEADING_WORDS or title.endswith(('.', ',', ';')):
        return False
    first = next((c for c in title if _LETTER_RE.match(c)), '')
    return not first.isascii() or first.isupper()


def is_heading_line(line: LineStyle, body_size: float) -> bool:
    text = _clean(line.text)
    if not text or len(text) > MAX_HEADING_LENGTH:
        return False
    if not (line.size >= body_size + MIN_SIZE_GAP or line.bold):
        return False
    numbered = _title_part(text)
    if numbered is not None:
        return _looks_like_title(numbered)
    return text.rstrip(':.').strip().lower() in STANDARD_HEADINGS


def find_body_size(sizes: Iterable[tuple[float, int]]) -> Optional[float]:
    """(글자 크기, 글자 수) 목록에서 글자 수가 가장 많은 크기를 본문 크기로 본다."""
    counts: Counter = Counter()
    for size, count in sizes:
        counts[round(size, 1)] += count
    return counts.most_common(1)[0][0] if counts else None


def detect_headings(page_text: str, lines: list, body_size: Optional[float]) -> list:
    """페이지 줄 목록에서 큰 제목을 찾아 page_text 안의 위치와 함께 돌려준다."""
    if body_size is None:
        return []
    headings = []
    position = 0
    for line in lines:
        offset = page_text.find(line.text, position)
        if offset < 0:
            continue  # 텍스트에서 위치를 못 찾으면 제목으로 쓰지 않는다
        position = offset + len(line.text)
        if is_heading_line(line, body_size):
            headings.append(PageHeading(_clean(line.text), offset))
    return headings


def make_section_id(order: int, title: str) -> str:
    """문서 안 순서와 제목으로 읽기 쉬운 ID를 만든다. 예: 03-model-architecture."""
    title_part = _title_part(_clean(title)) or _clean(title)
    slug = re.sub(r'[^0-9a-z가-힣]+', '-', title_part.lower()).strip('-')[:40].strip('-')
    return f'{order:02d}-{slug or "section"}'
