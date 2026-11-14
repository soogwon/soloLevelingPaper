"""PDF 첫 페이지 여백의 arXiv 표기(예: arXiv:1706.03762v7)에서 논문 번호를 읽는다.

본문 추출은 회전 문자를 빼지만 arXiv 표기는 세로로 찍히므로 첫 페이지 문자를 직접 읽는다.
실패하면 None을 돌려주며, 호출자는 판본 연결 없이 기존처럼 동작한다.
"""

from solo_leveling.domain.knowledge_graph import arxiv_base_id


def read_arxiv_id(pdf_path: str) -> str | None:
    try:
        import pdfplumber

        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return None
            text = ''.join(char.get('text', '') for char in pdf.pages[0].chars)
    except Exception:
        return None
    return arxiv_base_id(text)
