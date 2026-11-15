"""#391 고정 로컬 ONNX 산출물 캡처. 내보내기나 다운로드를 수행하지 않는다."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.embedding.onnx_capture import capture, worker  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/embedding/cases.json")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--file-name", default="onnx/model.onnx")
    parser.add_argument("--output", type=Path, required=True)
    for key, default in (("repeats", 3), ("warm-repeats", 3), ("max-length", 128),
                         ("batch-size", 8), ("threads", 1), ("timeout", 240), ("started-ns", 0)):
        parser.add_argument("--" + key, type=int, default=default)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.worker:
            worker(args.dataset, args.model_dir, args.file_name, args.output, args.started_ns,
                   max_length=args.max_length, batch_size=args.batch_size, threads=args.threads,
                   warm_repeats=args.warm_repeats)
        else:
            capture(args.dataset, args.model_dir, args.file_name, args.output, repeats=args.repeats,
                    warm_repeats=args.warm_repeats, max_length=args.max_length, batch_size=args.batch_size,
                    threads=args.threads, timeout=args.timeout)
            print("ONNX 후보 캡처 완료. 기준선 비교와 합격 판정은 별도 단계입니다.")
        return 0
    except Exception:
        print("CAPTURE_FAILED: 고정 ONNX 산출물·의존성·입력·출력 경로를 확인하세요.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
