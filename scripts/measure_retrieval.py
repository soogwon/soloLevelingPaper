"""실제 로컬 DB·색인·임베딩으로 근거 청크의 검색 순위를 비교한다(#284).

읽기 전용이다. 번역·임베딩을 새로 만들지 않고, 외부 API를 호출하지 않는다.
출력에는 순위·페이지·섹션·청크 번호와 일치 여부만 나오며 원문·번역문은 출력하지 않는다.

예:
    python scripts/measure_retrieval.py --data-root ~/solo-leveling-data \\
        --context-id cdf64b60-7667-4dcc-92f5-f43917c43ea2 \\
        --question "Multi-head attention은 왜 여러 개의 head를 쓰나요?" \\
        --needle "representation subspaces"
"""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from solo_leveling.application.evidence_qa.search import ContextSearchService  # noqa: E402
from solo_leveling.infrastructure.retrieval.sqlite_chroma import (  # noqa: E402
    SQLiteChromaRetriever, SQLiteContextReader,
)
from solo_leveling.infrastructure.storage.vector_store import get_client  # noqa: E402
from solo_leveling.interfaces.mcp.runtime import runtime_paths  # noqa: E402


def _normalize(text: str) -> str:
    return ' '.join(text.split()).lower()


def _measure(service, args, needle, *, pages=()):
    result = service.search(args.question, args.context_id, top_k=args.top_k, pdf_pages=pages)
    rows, hit = [], None
    for item in result.items:
        chunk = item.chunk
        matched = needle in _normalize(chunk.original_text)
        if matched and hit is None:
            hit = item.rank
        rows.append(f'  {item.rank:>2}. p{chunk.pdf_page:<3} #{chunk.chunk_index:<4} '
                    f'section={chunk.section_id or "-"} score={item.score:.3f}{"  <== 근거" if matched else ""}')
    return result.retrieval_method.value, hit, rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='근거 청크 검색 순위 비교(읽기 전용)')
    parser.add_argument('--data-root', type=Path, required=True, help='MCP 서버의 DATA_ROOT')
    parser.add_argument('--sqlite-filename', default='solo_leveling.sqlite3', help='설치 설정의 SQLITE_FILENAME')
    parser.add_argument('--chroma-subdir', default='chroma', help='설치 설정의 CHROMA_SUBDIR')
    parser.add_argument('--context-id', required=True)
    parser.add_argument('--question', required=True)
    parser.add_argument('--needle', required=True, help='근거 청크 원문에 들어 있는 짧은 구절')
    parser.add_argument('--page', type=int, action='append', default=[], help='비교용 페이지 제한')
    parser.add_argument('--top-k', type=int, default=10)
    args = parser.parse_args(argv)

    paths = runtime_paths({'DATA_ROOT': str(args.data_root), 'SQLITE_FILENAME': args.sqlite_filename,
                           'CHROMA_SUBDIR': args.chroma_subdir})
    if not paths.db_path.is_file():
        print('DB 파일을 찾을 수 없습니다. --data-root를 확인하세요.', file=sys.stderr)
        return 2
    client = get_client(str(paths.chroma_dir))
    contexts = SQLiteContextReader(str(paths.db_path))
    needle = _normalize(args.needle)
    for label, hybrid in (('변경 전(벡터만)', False), ('변경 후(벡터+어휘)', True)):
        service = ContextSearchService(contexts, SQLiteChromaRetriever(str(paths.db_path), client, hybrid=hybrid))
        method, hit, rows = _measure(service, args, needle)
        print(f'[{label}] method={method} 근거 순위={hit or f"상위 {args.top_k}위 밖"}')
        print('\n'.join(rows))
        if args.page:
            method, hit, _ = _measure(service, args, needle, pages=tuple(args.page))
            print(f'  페이지 {args.page} 제한 시 근거 순위={hit or f"상위 {args.top_k}위 밖"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
