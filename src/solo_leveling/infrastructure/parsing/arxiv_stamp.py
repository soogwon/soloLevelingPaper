"""관리 PDF의 첫 페이지 arXiv 표기로 검수 논문과의 관계를 확인한다."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryFile

from solo_leveling.domain.knowledge_graph import arxiv_base_id
from solo_leveling.infrastructure.storage.pdf_source import open_pdf_source


def read_arxiv_id(pdf_path: str, *, pdf_root: Path | None, expected_hash: str) -> str | None:
    if pdf_root is None:
        return None
    try:
        import pdfplumber

        root = pdf_root.resolve()
        source = Path(pdf_path)
        if not source.is_absolute():
            source = root / source
        # 경로 교체·링크를 거부하고 저장 해시와 같은 사본만 파싱한다.
        with open_pdf_source(source, root) as stream, TemporaryFile() as snapshot:
            digest = sha256()
            while block := stream.read(1024 * 1024):
                digest.update(block)
                snapshot.write(block)
            if digest.hexdigest() != expected_hash:
                return None
            snapshot.seek(0)
            with pdfplumber.open(snapshot) as pdf:
                if not pdf.pages:
                    return None
                # 본문의 다른 논문 링크를 현재 논문의 표기로 오인하지 않는다.
                text = ''.join(char.get('text', '') for char in pdf.pages[0].chars
                               if not char.get('upright', True))
    except Exception:
        return None
    return arxiv_base_id(text)
