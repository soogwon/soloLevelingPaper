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
HEADING = re.compile(r'^(?:\d+(?:\.\d+){0,2}|[A-Z](?:\.\d+)?)\s+[A-Z][\w\- ,.:()&/]{2,80}$')
MAX_SECTION_NUMBER = 15  # 그래프 눈금(예: '75 BiT …')을 절 번호로 착각하지 않게 한다.
# 번호 없이 단독으로 쓰는 절 이름. 표 안에 자주 홀로 나오는 'model', 'method' 같은 단어는 넣지 않는다.
NAMED = ('abstract', 'introduction', 'related work', 'background', 'experiments', 'results', 'discussion',
         'conclusion', 'conclusions', 'limitations', 'references', 'appendix', 'acknowledgements')
# 서비스(MCP) PDF 추출과 같은 값. 기본값(3)은 글자 간격이 좁은 논문에서 단어가 붙는다.
X_TOLERANCE = 1.5
Y_TOLERANCE = 5

# 가운데에 충분한 여백과 양쪽 본문이 확인된 페이지에만 단 분리를 적용한다.
# 표·그림 설명 등 폭 전체 줄이 본문에 섞이면 기존 추출을 유지한다.
GUTTER_MARGIN = 9.0
HEADER_ZONE = 0.45


def _lines(page) -> list[list[dict]]:
    rows: dict[int, list[dict]] = {}
    for word in page.extract_words(x_tolerance=X_TOLERANCE, y_tolerance=Y_TOLERANCE):
        rows.setdefault(round(word['top'] / 3), []).append(word)
    return [rows[key] for key in sorted(rows)]


def page_text(page) -> str:
    plain = page.extract_text(x_tolerance=X_TOLERANCE, y_tolerance=Y_TOLERANCE) or ''
    lines = _lines(page)
    if len(lines) < 10:
        return plain
    x0, top, x1, bottom = page.bbox
    mid = (x0 + x1) / 2

    def crosses(line):
        return any(w['x0'] < mid + GUTTER_MARGIN and w['x1'] > mid - GUTTER_MARGIN for w in line)

    # 폭 전체 영역은 본문보다 앞선 연속 영역만 허용한다.
    split = top
    body_started = False
    left_rows = right_rows = 0
    for line in lines:
        if crosses(line):
            if body_started or min(w['top'] for w in line) >= top + page.height * HEADER_ZONE:
                return plain
            split = max(split, max(w['bottom'] for w in line) + 0.5)
            continue
        left = any(w['x1'] <= mid - GUTTER_MARGIN for w in line)
        right = any(w['x0'] >= mid + GUTTER_MARGIN for w in line)
        body_started = True
        left_rows += left
        right_rows += right
    if min(left_rows, right_rows) < 5:
        return plain

    parts = []
    if split > top:
        parts.append(page.crop((x0, top, x1, split)))
    parts.append(page.crop((x0, split, mid, bottom)))
    parts.append(page.crop((mid, split, x1, bottom)))
    texts = [p.extract_text(x_tolerance=X_TOLERANCE, y_tolerance=Y_TOLERANCE) or '' for p in parts]
    return '\n'.join(t for t in texts if t.strip())


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
                pages.append(page_text(upright))
        match = ARXIV.search(stamp)
        return pages, match.group(1) if match else None
    from pypdf import PdfReader  # pdfplumber가 없을 때의 대안. 도장 판별은 하지 않는다.
    reader = PdfReader(str(path))
    return [page.extract_text() or '' for page in reader.pages], None


def looks_like_heading(line: str) -> bool:
    """그림 안의 글자(한 글자씩 흩어진 라벨, 그래프 눈금)는 절 제목 후보에서 뺀다."""
    number, _, rest = line.partition(' ')
    top = number.split('.')[0]
    if top.isdigit() and (top.startswith('0') or int(top) > MAX_SECTION_NUMBER):
        return False
    tokens = rest.split()
    return sum(len(t) == 1 for t in tokens) <= len(tokens) * 0.4


def headings(pages: list[str]) -> list[dict]:
    found = []
    for number, text in enumerate(pages, start=1):
        for line in text.splitlines():
            line = line.strip()
            if not line or len(line) > 90:
                continue
            if line.lower().rstrip(':') in NAMED or (HEADING.match(line) and looks_like_heading(line)):
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
    # 이전 논문의 페이지와 새 추출 결과가 섞이지 않게 기존 산출물은 덮어쓰지 않는다.
    if (args.out / 'paper.json').exists() or any(args.out.glob('page_*.txt')):
        print('기존 추출 결과가 있습니다. 다른 --out 폴더를 지정하세요.', file=sys.stderr)
        return 1
    try:
        pages, arxiv_id = extract_pages(args.pdf)
    except Exception as error:  # 손상·암호·PDF 아님. 내부 오류 대신 사용자에게 전할 문장을 낸다.
        print(f'PDF를 열 수 없습니다: {args.pdf.name} ({type(error).__name__}). '
              '파일이 손상됐거나, 암호가 걸렸거나, PDF가 아닐 수 있습니다. 다른 파일을 올려 달라고 안내하세요.',
              file=sys.stderr)
        return 2
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
