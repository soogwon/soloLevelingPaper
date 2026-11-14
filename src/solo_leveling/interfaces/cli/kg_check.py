"""검수 KG 시드를 검증하고, 기준 PDF의 해시를 시드 논문과 대조한다.

사용 예:
    solo-leveling-kg-check                       # 패키지 기본 시드 검증
    solo-leveling-kg-check path/to/seed.json     # 다른 시드 검증
    solo-leveling-kg-check --hash attention.pdf  # PDF 해시 출력과 시드 대조
    solo-leveling-kg-check --verify-pdf a.pdf    # 해시로 논문을 찾아 각 개념의 검색어가 적힌 페이지에 있는지 확인
"""

import argparse
import hashlib
from pathlib import Path
import re
import sys
import unicodedata

from solo_leveling.domain.knowledge_graph import SeedValidationError, learning_path, GOALS
from solo_leveling.knowledge import load_graph


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            digest.update(block)
    return digest.hexdigest()


def normalize_text(text: str) -> str:
    """PDF 추출 텍스트와 검색어를 같은 기준으로 비교한다.

    합자(ﬁ)는 풀고, 줄끝 하이픈이 원래 하이픈인지 단어 분리인지 알 수 없으므로
    하이픈과 공백을 모두 지운 뒤 소문자로 비교한다.
    """
    return re.sub(r'[\s\-\u00ad\u2010\u2011]+', '', unicodedata.normalize('NFKC', text)).lower()


def page_texts(path: Path) -> list[str]:
    import pdfplumber  # 로컬 의존성. 검증할 때만 필요하다.
    with pdfplumber.open(path) as pdf:
        return [normalize_text(page.extract_text() or '') for page in pdf.pages]


def verify_phrases(paper, pages: list[str]) -> list[str]:
    """검색어가 시드에 적은 페이지 중 하나에 실제로 나오는지 확인하고 문제를 돌려준다."""
    problems = []
    for cid, location in paper.concepts.items():
        if not location.search_phrase:
            continue
        phrase = normalize_text(location.search_phrase)
        found = [n for n, text in enumerate(pages, start=1) if phrase in text]
        if not set(found) & set(location.pdf_pages):
            where = ', '.join(map(str, found[:8])) or '없음'
            problems.append(f'{paper.paper_key}/{cid}: "{location.search_phrase}"가 '
                            f'{list(location.pdf_pages)}페이지에 없습니다(발견 페이지: {where}).')
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='solo-leveling-kg-check', description='검수 KG 시드 검증')
    parser.add_argument('seed', nargs='?', help='시드 JSON 경로(생략 시 패키지 기본 시드)')
    parser.add_argument('--hash', nargs='+', metavar='PDF', default=[],
                        help='PDF의 SHA-256을 출력하고 시드 논문과 대조')
    parser.add_argument('--verify-pdf', nargs='+', metavar='PDF', default=[],
                        help='해시로 시드 논문을 찾아 개념별 검색어가 적힌 페이지에 있는지 확인')
    args = parser.parse_args(argv)
    try:
        graph = load_graph(args.seed)
    except SeedValidationError as error:
        print(f'시드 검증 실패: 문제 {len(error.problems)}개', file=sys.stderr)
        for problem in error.problems:
            print(f'  - {problem}', file=sys.stderr)
        return 1
    except OSError as error:
        print(f'시드 파일을 읽지 못했습니다: {error.strerror}', file=sys.stderr)
        return 1

    print(f'시드 검증 통과: {graph.revision_id}, 개념 {len(graph.concepts)}개, '
          f'선수 관계 {len(graph.prerequisites)}개, 논문 {len(graph.papers)}편')
    for warning in graph.warnings:
        print(f'  경고: {warning}')
    for paper in graph.papers:
        sizes = ', '.join(f'{goal} {len(learning_path(graph, paper, goal).steps)}' for goal in GOALS)
        print(f'  {paper.paper_key}: 경로 길이 {sizes}')

    status = 0
    for name in args.hash:
        path = Path(name)
        if not path.is_file():
            print(f'{name}: 파일을 찾을 수 없습니다.', file=sys.stderr)
            status = 1
            continue
        digest = file_sha256(path)
        paper = graph.paper_by_hash(digest)
        match = f'시드의 {paper.paper_key}와 일치' if paper else '시드에 일치하는 논문 없음'
        print(f'{digest}  {name}  ({match})')

    for name in args.verify_pdf:
        path = Path(name)
        paper = graph.paper_by_hash(file_sha256(path)) if path.is_file() else None
        if paper is None:
            print(f'{name}: 파일이 없거나 시드에 해시가 같은 논문이 없습니다.', file=sys.stderr)
            status = 1
            continue
        try:
            problems = verify_phrases(paper, page_texts(path))
        except ImportError:
            print('PDF 검증에는 pdfplumber가 필요합니다: pip install -e ".[local]"', file=sys.stderr)
            return 1
        checked = sum(1 for location in paper.concepts.values() if location.search_phrase)
        if problems:
            status = 1
            print(f'{name}: {paper.paper_key} 검색어 {checked}개 중 문제 {len(problems)}개', file=sys.stderr)
            for problem in problems:
                print(f'  - {problem}', file=sys.stderr)
        else:
            print(f'{name}: {paper.paper_key} 검색어 {checked}개가 모두 적힌 페이지에 있습니다.')
    return status


if __name__ == '__main__':
    raise SystemExit(main())
