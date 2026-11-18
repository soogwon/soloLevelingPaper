"""팀이 검수한 개념 지도(data/seed.json)로 학습 순서와 선수 개념을 계산한다.

검수된 논문(현재 Attention, BERT, ViT)일 때만 결과가 있다. 다른 논문이면 graph_status=no_graph를
돌려준다. 이때 학습 순서를 지어내지 말고, 일반 읽기 전략(references/reading-strategies.md)을 쓴다.

사용법:
    python concept_path.py --paper-json paper_text/paper.json --goal understand --known "RNN,행렬 곱"
    python concept_path.py --paper-json paper_text/paper.json --concept "멀티헤드 어텐션"
    python concept_path.py --list            # 검수 논문과 개념 이름 목록

계산은 서비스(MCP)의 get_learning_path·get_prerequisites와 같은 코드(knowledge_graph.py)를 쓴다.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from knowledge_graph import GOALS, learning_path, parse_seed  # noqa: E402

SEED = HERE.parent / 'data' / 'seed.json'


def concept_view(c) -> dict:
    return {'concept_id': c.concept_id, 'name_ko': c.name_ko, 'name_en': c.name_en,
            'description_ko': c.description_ko}


def locations(loc, hide: bool) -> list[dict]:
    if loc is None or hide:
        return []
    return [{'pdf_page': p, 'section_hint': loc.section_hint, 'search_phrase': loc.search_phrase}
            for p in loc.pdf_pages]


def find_paper(graph, info: dict):
    """(논문, 상태). 같은 파일이면 ready, arXiv 번호만 같으면 related_version(쪽 위치는 숨김)."""
    paper = graph.paper_by_hash(info.get('sha256', ''))
    if paper is not None:
        return paper, 'ready'
    paper = graph.paper_by_arxiv(info.get('arxiv_id'))
    if paper is not None:
        return paper, 'related_version'
    return None, 'no_graph'



def use_utf8_console() -> None:
    """Windows 콘솔(cp1252 등)에서도 한글 출력이 멈추지 않게 표준 출력을 UTF-8로 맞춘다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    use_utf8_console()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--paper-json', type=Path, help='pdf_text.py가 만든 paper.json')
    parser.add_argument('--goal', choices=GOALS, default='understand')
    parser.add_argument('--known', default='', help='이미 아는 개념, 쉼표로 구분')
    parser.add_argument('--mode', choices=('compact', 'expanded'), default='compact')
    parser.add_argument('--concept', help='이 개념의 선수 개념만 조회')
    parser.add_argument('--list', action='store_true')
    args = parser.parse_args(argv)

    graph = parse_seed(SEED.read_bytes())
    if args.list:
        print(json.dumps({
            'papers': [{'key': p.paper_key, 'label': p.label, 'source_url': p.source_url} for p in graph.papers],
            'concepts': [f'{c.name_ko} ({c.name_en})' for c in graph.concepts.values()],
        }, ensure_ascii=False, indent=2))
        return 0
    if args.paper_json is None:
        parser.error('--paper-json 또는 --list가 필요합니다.')

    info = json.loads(args.paper_json.read_text(encoding='utf-8'))
    paper, status = find_paper(graph, info)
    result = {'graph_status': status, 'graph_revision_id': graph.revision_id if paper else None,
              'paper': ({'label': paper.label, 'source_url': paper.source_url} if paper else None)}
    if paper is None:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    hide = status == 'related_version'  # 쪽 위치는 검수 판본 기준이라 다른 판본에는 보여 주지 않는다.

    if args.concept:
        cid = graph.resolve(args.concept)
        if cid is None:
            result['error'] = '개념 지도에 없는 개념입니다. 선수 관계를 지어내지 마세요.'
        else:
            result.update({
                'concept': concept_view(graph.concepts[cid]),
                'prerequisites': [{'name_ko': graph.concepts[e.prerequisite_id].name_ko,
                                   'rationale_ko': e.rationale_ko} for e in graph.requires(cid)],
                'required_by': [graph.concepts[e.concept_id].name_ko for e in graph.required_by(cid)],
                'paper_locations': locations(paper.concepts.get(cid), hide),
            })
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    known = [k.strip() for k in args.known.split(',') if k.strip()]
    path = learning_path(graph, paper, args.goal, known, args.mode)
    result.update({
        'goal': args.goal, 'mode': args.mode,
        'steps': [{
            'order': s.order, **concept_view(s.concept), 'role': s.role, 'reason_ko': s.reason_ko,
            'needed_for': s.needed_for.name_ko if s.needed_for else None,
            'paper_locations': locations(s.locations, hide),
        } for s in path.steps],
        'skipped': [{'name_ko': s.concept.name_ko, 'reason': s.reason} for s in path.skipped],
        'unresolved_concepts': list(path.unresolved),
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
