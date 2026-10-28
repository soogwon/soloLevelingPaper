"""합성 평가 자료로 오프라인 집계만 실행한다. 실제 API 옵션은 제공하지 않는다."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from evaluation.semantic.runner import OfflineVerifier, evaluate, load_dataset


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=('development', 'holdout', 'all'), default='development')
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--max-calls', type=int, default=100)
    args = parser.parse_args(argv)
    try:
        data, digest = load_dataset(ROOT / 'evaluation/semantic/cases.json')
        result = evaluate(data, digest, OfflineVerifier(), split=args.split,
                          repeats=args.repeats, max_calls=args.max_calls)
    except ValueError as error:
        parser.error(str(error))
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(['git', 'status', '--porcelain', '--untracked-files=normal'],
                               cwd=ROOT, capture_output=True, text=True, check=True).stdout)
    result.update(mode='offline', quality_measurement=False,
                  notice='고정 응답 대역의 집계 점검 결과이며 실제 모델 품질이 아닙니다.',
                  python_version=platform.python_version(), code_revision=revision,
                  working_tree_dirty=dirty, created_at=datetime.now(timezone.utc).isoformat())
    # 실행 중인 소스도 해시로 기록해 미커밋 상태의 비교를 구분한다.
    import hashlib
    paths = [Path(__file__), ROOT / 'evaluation/semantic/runner.py',
             ROOT / 'src/solo_leveling/application/evidence_qa/semantic.py',
             ROOT / 'src/solo_leveling/domain/semantic_verification.py']
    result['source_hashes'] = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in paths}
    target = ROOT / 'runtime/semantic-evaluation'
    target.mkdir(parents=True, exist_ok=True)
    output = target / f'offline-{uuid4().hex}.json'
    with output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print('오프라인 점검 완료. 실제 모델 품질 평가가 아닙니다.')
    print(f"실행 수: {len(result['results'])}, 결과: {output}")
    return 0


if __name__ == '__main__':
    # Windows의 비 UTF-8 콘솔에서도 한국어 안내와 경로를 출력한다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
