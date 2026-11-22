"""infrastructure/parsing — 첫 페이지 앞부분(front matter)에서 논문 서지 정보 찾기

지금은 논문 제목만 찾는다. 저자·출판 연도는 같은 PaperFrontMatter에 나중에 채운다.

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


def detect_front_matter(lines: Iterable[LineStyle], body_size: Optional[float], *,
                        excluded_texts: Iterable[str] = ()) -> Optional[PaperFrontMatter]:
    """찾은 서지 정보가 하나도 없으면 None을 돌려준다."""
    title = detect_title(lines, body_size, excluded_texts=excluded_texts)
    return PaperFrontMatter(title=title) if title is not None else None
