"""
infrastructure/parsing — PDF 페이지 보존 추출

22번 문서: "pdfplumber, 페이지 단위 추출·청킹"
페이지 정보를 절대 잃지 않는 게 핵심이다 — 이전 프로토타입(levelup-paper)에서
전체를 이어붙인 뒤 청킹해 페이지 정보가 소실됐던 문제를 여기서 근본적으로 막는다.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import List

import pdfplumber


@dataclass
class ExtractedPage:
    pdf_page: int  # 1부터 시작하는 물리 페이지 번호
    text: str
    printed_page_label: str | None = None  # 자동 인식 방식 미정(22번 문서) — 현재는 항상 None


def extract_pages(pdf_path: str) -> List[ExtractedPage]:
    """
    PDF를 페이지 단위로 추출한다. 절대 전체를 하나의 문자열로 합치지 않는다.
    printed_page_label(인쇄된 페이지 번호) 자동 인식은 22번 문서에서 "미정"으로
    명시되어 있어, 여기서는 항상 None을 반환한다 — 추측해서 채우지 않는다.
    """
    pages: List[ExtractedPage] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append(ExtractedPage(pdf_page=i, text=text, printed_page_label=None))
    return pages


def page_count(pdf_path: str) -> int:
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)
