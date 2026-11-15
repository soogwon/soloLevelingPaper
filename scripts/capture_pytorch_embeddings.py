"""#391 로컬 PyTorch 기준선 캡처. 고정 snapshot만 사용하며 다운로드하지 않는다."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.embedding.pytorch_capture import capture, worker  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset', type=Path, default=ROOT / 'evaluation/embedding/cases.json')
    p.add_argument('--model-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    for key, default in [('repeats', 3), ('warm-repeats', 3), ('max-length', 128),
                         ('batch-size', 8), ('threads', 1), ('timeout', 240), ('started-ns', 0)]:
        p.add_argument('--' + key, type=int, default=default)
    p.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    try:
        if a.worker:
            worker(a.dataset, a.model_dir, a.output, a.started_ns, max_length=a.max_length,
                   batch_size=a.batch_size, threads=a.threads, warm_repeats=a.warm_repeats)
        else:
            capture(a.dataset, a.model_dir, a.output, repeats=a.repeats, warm_repeats=a.warm_repeats,
                    max_length=a.max_length, batch_size=a.batch_size, threads=a.threads, timeout=a.timeout)
            print('PyTorch 기준선 캡처 완료. ONNX 비교·합격 판정은 미실행입니다.')
        return 0
    except Exception:
        # 라이브러리 오류에 입력 본문·로컬 경로가 섞일 수 있으므로 고정 메시지만 표시한다.
        print('CAPTURE_FAILED: 로컬 모델·의존성·입력·전용 출력 경로를 확인하세요.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
