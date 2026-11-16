"""논문 PDF를 페이지별 텍스트로 풀고, 지문(SHA-256)·arXiv 번호·절 제목 후보를 알려 준다.

사용법:
    python pdf_text.py 논문.pdf [--out 출력폴더]

출력 폴더(기본: ./paper_text)에 다음을 만든다.
    page_001.txt …   페이지별 본문 (PDF 쪽 번호 = 파일 번호)
    paper.json       sha256, arxiv_id, 쪽수, 절 제목 후보, 페이지별 글자 수
표준 출력에는 요약만 찍는다. 본문 전체를 출력하지 않으므로 필요한 페이지 파일만 골라 읽는다.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ARXIV = re.compile(r'(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)(\d{4}\.\d{4,5})(?:v\d+)?', re.IGNORECASE)
# "3 Model Architecture", "3.2.1 Scaled Dot-Product Attention", "A Appendix" 같은 줄
HEADING = re.compile(r'^(?:\d+(?:\.\d+){0,2}|[A-Z](?:\.\d+)?)\s+[A-Z][\w\- ,:()&/]{2,80}$')
# 번호 없이 단독으로 쓰는 절 이름. 표 안에 자주 홀로 나오는 'model', 'method' 같은 단어는 넣지 않는다.
NAMED = ('abstract', 'introduction', 'related work', 'background', 'experiments', 'results', 'discussion',
         'conclusion', 'conclusions', 'limitations', 'references', 'appendix', 'acknowledgements')
# 서비스(MCP) PDF 추출과 같은 값. 기본값(3)은 글자 간격이 좁은 논문에서 단어가 붙는다.
X_TOLERANCE = 1.5
Y_TOLERANCE = 5


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def extract_pages(path: Path) -> tuple[list[str], str | None]:
    """페이지별 텍스트와, 첫 페이지 세로 글자(arXiv 도장)에서 찾은 arXiv 번호."""
    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None
    if pdfplumber is not None:
        pages, stamp = [], ''
        with pdfplumber.open(str(path)) as pdf:
            for index, page in enumerate(pdf.pages):
                if index == 0:
                    # arXiv 도장은 세로로 찍혀 있다. 본문 속 다른 논문 링크와 섞이지 않게 세로 글자만 본다.
                    stamp = ''.join(c.get('text', '') for c in page.chars if not c.get('upright', True))
                # 세로 글자(arXiv 도장 등)는 본문에서 뺀다.
                upright = page.filter(lambda o: o.get('object_type') != 'char' or o.get('upright', True))
                pages.append(upright.extract_text(x_tolerance=X_TOLERANCE, y_tolerance=Y_TOLERANCE) or '')
        match = ARXIV.search(stamp)
        return pages, match.group(1) if match else None
    from pypdf import PdfReader  # pdfplumber가 없을 때의 대안. 도장 판별은 하지 않는다.
    reader = PdfReader(str(path))
    return [page.extract_text() or '' for page in reader.pages], None


def headings(pages: list[str]) -> list[dict]:
    found = []
    for number, text in enumerate(pages, start=1):
        for line in text.splitlines():
            line = line.strip()
            if not line or len(line) > 90:
                continue
            if HEADING.match(line) or line.lower().rstrip(':') in NAMED:
                found.append({'pdf_page': number, 'heading': line})
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--out', type=Path, default=Path('paper_text'))
    args = parser.parse_args(argv)
    if not args.pdf.is_file():
        print(f'PDF를 찾을 수 없습니다: {args.pdf}', file=sys.stderr)
        return 1
    pages, arxiv_id = extract_pages(args.pdf)
    args.out.mkdir(parents=True, exist_ok=True)
    for number, text in enumerate(pages, start=1):
        (args.out / f'page_{number:03d}.txt').write_text(text, encoding='utf-8')
    info = {
        'file': args.pdf.name,
        'sha256': sha256_of(args.pdf),
        'arxiv_id': arxiv_id,
        'page_count': len(pages),
        'chars_per_page': [len(t) for t in pages],
        'headings': headings(pages),
    }
    (args.out / 'paper.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    empty = [i + 1 for i, t in enumerate(pages) if len(t.strip()) < 50]
    print(f'쪽수: {len(pages)} · sha256: {info["sha256"][:16]}… · arXiv: {arxiv_id or "확인 안 됨"}')
    print(f'절 제목 후보 {len(info["headings"])}개 · 결과 폴더: {args.out}')
    if empty:
        print(f'글자가 거의 없는 쪽: {empty} (스캔본·그림 페이지일 수 있음)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
