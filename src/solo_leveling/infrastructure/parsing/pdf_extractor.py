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
"""
from dataclasses import dataclass
from pathlib import Path
from typing import List

import pdfplumber

# pdfplumber extract_text() 기본값(3)은 일부 논문 PDF에서 단어 간 공백을
# 인식하지 못해 단어가 붙어버리는 문제가 있었다. 실측 검증 결과 1.5가
# 안정적으로 공백을 보존함 (근거: 진단 스크립트로 1/1.5/2 모두 정상 확인).
TEXT_X_TOLERANCE = 1.5


@dataclass
class ExtractedPage:
    pdf_page: int  # 1부터 시작하는 물리 페이지 번호
    text: str
    printed_page_label: str | None = None  # 자동 인식 방식 미정(22번 문서) → 현재는 항상 None


def extract_pages(pdf_path: str) -> List[ExtractedPage]:
    """
    PDF를 페이지 단위로 추출한다. 절대 전체를 하나의 문자열로 합치지 않는다.
    printed_page_label(인쇄된 페이지 번호) 자동 인식은 22번 문서에서 "미정"으로
    명시되어 있어, 여기서는 항상 None을 반환하며 추측해서 채우지 않는다.
    """
    pages: List[ExtractedPage] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=TEXT_X_TOLERANCE) or ""
            pages.append(ExtractedPage(pdf_page=i, text=text, printed_page_label=None))
    return pages


def page_count(pdf_path: str) -> int:
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)
