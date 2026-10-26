"""
infrastructure/parsing — PDF 페이지 보존 추출

22번 문서: "pdfplumber, 페이지 단위 추출·청킹"
페이지 정보를 잃지 않는 게 핵심이다. 이전 프로토타입(levelup-paper)에서
전체를 이어붙인 뒤 청킹하며 페이지 정보가 사라졌던 문제를 여기서 근본적으로 막는다.

x_tolerance 관련(2026-09-27 발견): pdfplumber의 extract_text() 기본값
(x_tolerance=3)은 문자 간격이 좁은 논문 PDF(예: arXiv 1706.03762)에서
단어 사이 공백을 인식하지 못해 "Providedproperattribution..."처럼
단어가 붙어버리는 문제가 있었다. x_tolerance=1~2 범위에서 실제 논문
PDF로 검증했을 때 정상적으로 공백이 보존됨을 확인, 1.5로 고정했다.
extract_words() 기반 재구성은 오히려 단어 자체를 잘못 묶어 도움이 안 됐다
(같은 근본 원인이 단어 경계 판정에도 영향을 주기 때문).
다른 폰트/PDF에서 이 값이 안 맞으면 재조정이 필요할 수 있다 — 고정값이
아니라 경험적으로 튜닝한 값임을 유의.

회전 텍스트 관련(2026-09-30 발견): 일부 페이지(예: arXiv 1706.03762의
14~15페이지, 어텐션 시각화 그림)에는 그림에 박힌 세로/회전 캡션 텍스트가
있다. pdfplumber는 이런 회전 문자를 본문 읽기 순서에 그대로 끼워 넣다가
뒤죽박죽 뒤집힌 텍스트로 나오는 문제가 있었다(진단 스크립트로 문자별
`upright` 속성을 확인해 원인 확정). 이런 회전 텍스트는 애초에 읽을 수
있는 본문이 아니라 그림의 일부이므로, 추출 전에 upright=False인 문자를
걸러내서 본문 추출 결과에서 아예 빼도록 했다. 표는 같은 방식(회전 필터)
으로 검증했을 때 원래도 문제 없었음을 확인했다 — extract_tables()로
바꾸면 오히려 셀 병합 문제가 생겨 더 나빠지므로 표는 손대지 않았다.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import pdfplumber

from solo_leveling.infrastructure.parsing.sections import (
    LineStyle, detect_headings, find_body_size,
)

PDF_ENCRYPTED = "pdf_encrypted"
PDF_UNREADABLE = "pdf_unreadable"


class PdfExtractionError(ValueError):
    """PDF를 열 수 없을 때 쓰는 오류.

    라이브러리(pdfminer)의 내부 메시지나 경로를 담지 않고 고정 코드만 가진다.
    code는 작업 기록(limitations)에 그대로 남는다: pdf_encrypted | pdf_unreadable.
    """

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _open_pdf(pdf_path: str):
    """pdfplumber.open을 감싸 PDF 내용 문제(손상·암호·PDF 아님)만 PdfExtractionError로 바꾼다.

    파일이 없거나 권한이 없는 경우처럼 PDF 내용과 무관한 오류는 그대로 올린다.
    """
    try:
        return pdfplumber.open(pdf_path)
    except (FileNotFoundError, PermissionError, IsADirectoryError):
        raise
    except Exception as error:
        # pdfplumber는 pdfminer 예외를 PdfminerException(원래 예외)로 감싼다.
        cause = error.args[0] if error.args and isinstance(error.args[0], BaseException) else error
        if type(cause).__module__.split(".")[0] not in ("pdfminer", "pdfplumber"):
            raise
        if type(cause).__name__ in ("PDFPasswordIncorrect", "PDFEncryptionError"):
            raise PdfExtractionError(
                PDF_ENCRYPTED, "암호가 걸린 PDF는 처리할 수 없습니다.") from None
        raise PdfExtractionError(
            PDF_UNREADABLE, "PDF를 읽을 수 없습니다. 파일이 손상되었거나 PDF가 아닙니다.") from None


# pdfplumber extract_text() 기본값(3)은 일부 논문 PDF에서 단어 간 공백을
# 인식하지 못해 단어가 붙어버리는 문제가 있었다. 실측 검증 결과 1.5가
# 안정적으로 공백을 보존함 (근거: 진단 스크립트로 1/1.5/2 모두 정상 확인).
TEXT_X_TOLERANCE = 1.5


# 페이지 아래 여백(맨 아래 10%)에 숫자만 있는 줄은 쪽 번호로 보고 본문에서 뺀다(#15).
# 본문 안의 수식·표 숫자는 여백에 있지 않거나 다른 글자와 함께 있으므로 건드리지 않는다.
FOOTER_ZONE_RATIO = 0.90
_PAGE_NUMBER_RE = re.compile(r"\d{1,4}")


@dataclass
class ExtractedPage:
    pdf_page: int  # 1부터 시작하는 물리 페이지 번호
    text: str
    printed_page_label: str | None = None  # 자동 인식 방식 미정(22번 문서) → 현재는 항상 None
    # 이 페이지에서 시작하는 큰 섹션 제목(M1-05a). 못 찾으면 빈 목록이다.
    headings: list = field(default_factory=list)
    # 본문에서 분리한 쪽 번호 후보(#15). 저장하거나 인용에 쓰지 않는다.
    # printed_page_label로 쓸지는 #31에서 정한다.
    page_number_candidate: str | None = None


def _is_upright(obj: dict) -> bool:
    """char가 아닌 객체(선, 사각형 등)는 그대로 통과시키고, char만 회전
    여부를 검사한다. upright 키가 없는 경우(char가 아닌 경우)는 True로
    간주해 필터에 걸리지 않게 한다."""
    if obj.get("object_type") != "char":
        return True
    return obj.get("upright", True)


def _line_styles(page) -> list[LineStyle]:
    """페이지 각 줄의 글자 크기·굵기를 읽는다. 텍스트는 extract_text와 같은 줄 나누기를 쓴다."""
    styles = []
    for line in page.extract_text_lines(x_tolerance=TEXT_X_TOLERANCE, return_chars=True):
        chars = [c for c in line["chars"] if c["text"].strip()]
        if not chars:
            continue
        styles.append(LineStyle(
            text=line["text"],
            size=max(c["size"] for c in chars),
            bold=all("bold" in str(c.get("fontname", "")).lower() for c in chars),
        ))
    return styles


def split_page_number(page, text: str) -> tuple[str, str | None]:
    """페이지 아래 여백에 숫자만 있는 줄이 있으면 본문에서 떼어낸다.

    반환: (쪽 번호를 뺀 본문, 쪽 번호 후보). 아래 조건을 모두 만족할 때만 뗀다.
    - 맨 아래 FOOTER_ZONE_RATIO 이후 영역의 글자가 숫자 1~4자리 하나뿐이다.
    - 본문 텍스트의 마지막 줄이 바로 그 숫자다.
    조건이 하나라도 안 맞으면 본문을 그대로 돌려준다.
    """
    try:
        zone = page.crop((0, page.height * FOOTER_ZONE_RATIO, page.width, page.height))
        zone_text = (zone.extract_text(x_tolerance=TEXT_X_TOLERANCE) or "").strip()
    except Exception:
        return text, None
    if not _PAGE_NUMBER_RE.fullmatch(zone_text):
        return text, None
    body = text.rstrip()
    head, _, last_line = body.rpartition("\n")
    if last_line.strip() != zone_text:
        return text, None
    return head.rstrip(), zone_text


def extract_pages(pdf_path: str) -> List[ExtractedPage]:
    """
    PDF를 페이지 단위로 추출한다. 절대 전체를 하나의 문자열로 합치지 않는다.
    printed_page_label(인쇄된 페이지 번호) 자동 인식은 22번 문서에서 "미정"으로
    명시되어 있어, 여기서는 항상 None을 반환하며 추측해서 채우지 않는다.

    회전된(upright=False) 문자는 본문이 아니라 그림에 박힌 캡션/축 레이블인
    경우가 대부분이라 추출 전에 걸러낸다(위 docstring 참고).

    쪽 번호(#15): 페이지 아래 여백의 숫자만 있는 줄은 본문에서 떼어 page_number_candidate에 둔다.
    이 값은 printed_page_label을 채우는 데 쓰지 않는다(자동 인식 여부는 #31에서 결정).

    섹션 제목(M1-05a): 글자 크기·굵기로 큰 제목을 찾아 각 페이지의 headings에 담는다.
    제목 인식은 실패해도 본문 추출에 영향을 주지 않는다(제목 없이 진행).
    """
    pages: List[ExtractedPage] = []
    page_styles: list[list[LineStyle]] = []
    size_counts: list[tuple[float, int]] = []
    with _open_pdf(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            upright_only = page.filter(_is_upright)
            text = upright_only.extract_text(x_tolerance=TEXT_X_TOLERANCE) or ""
            text, page_number = split_page_number(upright_only, text)
            pages.append(ExtractedPage(pdf_page=i, text=text, printed_page_label=None,
                                       page_number_candidate=page_number))
            try:
                styles = _line_styles(upright_only)
            except Exception:
                styles = []
            page_styles.append(styles)
            size_counts.extend((line.size, len(line.text.strip())) for line in styles)
    body_size = find_body_size(size_counts)
    for extracted, styles in zip(pages, page_styles):
        extracted.headings = detect_headings(extracted.text, styles, body_size)
    return pages


def page_count(pdf_path: str) -> int:
    with _open_pdf(pdf_path) as pdf:
        return len(pdf.pages)
