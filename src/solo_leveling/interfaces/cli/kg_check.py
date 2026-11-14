"""검수 KG 시드를 검증하고, 기준 PDF의 해시를 시드 논문과 대조한다.

사용 예:
    solo-leveling-kg-check                       # 패키지 기본 시드 검증
    solo-leveling-kg-check path/to/seed.json     # 다른 시드 검증
    solo-leveling-kg-check --hash attention.pdf  # PDF 해시 출력과 시드 대조
"""

import argparse
import hashlib
from pathlib import Path
import sys

from solo_leveling.domain.knowledge_graph import SeedValidationError, learning_path, GOALS
from solo_leveling.knowledge import load_graph


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='solo-leveling-kg-check', description='검수 KG 시드 검증')
    parser.add_argument('seed', nargs='?', help='시드 JSON 경로(생략 시 패키지 기본 시드)')
    parser.add_argument('--hash', nargs='+', metavar='PDF', default=[],
                        help='PDF의 SHA-256을 출력하고 시드 논문과 대조')
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
    return status


if __name__ == '__main__':
    raise SystemExit(main())
