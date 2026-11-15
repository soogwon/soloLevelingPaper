"""#391 임베딩 캡처 비교. 모델 다운로드·API·DB 접근이 없는 오프라인 명령."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.embedding.comparison import (  # noqa: E402
    EvaluationError, compare, load_json, synthetic_capture,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=ROOT / 'evaluation/embedding/cases.json')
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--smoke', action='store_true', help='합성 벡터로 비교 경로만 확인')
    parser.add_argument('--output', type=Path, required=True, help='새 결과 파일(기존 파일 덮어쓰기 금지)')
    args = parser.parse_args(argv)
    if args.smoke == bool(args.baseline or args.candidate) or (not args.smoke and
                                                           not (args.baseline and args.candidate)):
        parser.error('--smoke 또는 --baseline/--candidate 쌍 중 하나를 지정하세요.')
    try:
        data = load_json(args.dataset)
        baseline = synthetic_capture(data) if args.smoke else load_json(args.baseline)
        candidate = baseline if args.smoke else load_json(args.candidate)
        report = compare(data, baseline, candidate)
        encoded = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
        # 비교가 끝나기 전에는 파일을 만들지 않고, 입력·기존 결과를 덮어쓰지 않는다.
        with args.output.open('x', encoding='utf-8') as handle:
            handle.write(encoded)
    except EvaluationError as exc:
        print(f'평가 실패: {exc}', file=sys.stderr)
        return 2
    except (OSError, TypeError, ValueError, KeyError, OverflowError):
        print('평가 실패: INPUT_OR_OUTPUT_INVALID', file=sys.stderr)
        return 2
    print(f'비교 완료: 질의 {len(report["queries"])}개, 합격 판정 보류')
    if args.smoke:
        print('합성 smoke 결과입니다. PyTorch·ONNX 품질·성능 실측이 아닙니다.')
    return 0


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
