"""저장된 두 평가 결과를 비교한다. 네트워크나 DB를 사용하지 않는다."""

import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from evaluation.semantic.runner import load_dataset
from evaluation.semantic.comparison import compare_results, load_result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, default=ROOT / 'evaluation/semantic/cases.json')
    args = parser.parse_args(argv)
    try:
        data, digest = load_dataset(args.dataset)
        result = compare_results(load_result(args.baseline), load_result(args.candidate), data, digest)
        target = ROOT / 'runtime/semantic-evaluation'
        target.mkdir(parents=True, exist_ok=True)
        output = target / f'comparison-{uuid4().hex}.json'
        with output.open('x', encoding='utf-8') as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
    except (ValueError, OSError):
        print('비교 실패: 결과 형식·자료 해시·선택 사례·반복 수·실행 제한이 일치하는지 확인하세요.', file=sys.stderr)
        return 2
    print(f'비교 결과: {output}')
    print('초안 포함 점수는 잠정 결과이며 실제 API는 호출하지 않았습니다.')
    return 0


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
