"""기본 실행은 계획만 출력한다. --live에서만 합성 자료를 외부로 전송한다."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from evaluation.semantic.runner import load_dataset
from evaluation.semantic.provider import select_cases, execution_plan, run_provider


def load_values(path, model):
    from dotenv import dotenv_values
    if not path.is_file():
        raise ValueError('EVALUATION_ENV_FILE_MISSING')
    values = {k: v for k, v in dotenv_values(path, encoding='utf-8-sig', interpolate=False).items()
              if v is not None}
    values.update(os.environ)
    values['VERIFICATION_MODEL'] = model
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--model')
    parser.add_argument('--split', choices=('development', 'holdout', 'all'), default='development')
    parser.add_argument('--case', action='append', default=[], dest='case_ids')
    parser.add_argument('--include-drafts', action='store_true')
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--max-calls', type=int, required=True)
    args = parser.parse_args(argv)
    try:
        data, digest = load_dataset(ROOT / 'evaluation/semantic/cases.json')
        selected = select_cases(data, split=args.split, case_ids=args.case_ids,
                                include_drafts=args.include_drafts)
        plan = execution_plan(selected, repeats=args.repeats, max_calls=args.max_calls)
        if not args.live:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            print('실제 API 호출 없음. 기본 대상은 사람 검토 완료 사례입니다.')
            return 0
        if not args.env_file or not args.model or not args.model.strip():
            raise ValueError('EVALUATION_EXPLICIT_CONFIG_REQUIRED')
        if not plan['selected_cases']:
            raise ValueError('EVALUATION_EMPTY_SELECTION')
        values = load_values(args.env_file, args.model)
    except (ValueError, OSError):
        # 설정 파일·모델 인자에 민감정보가 포함될 수 있어 원래 오류를 출력하지 않는다.
        print('평가 준비 실패: 선택 사례·검토 상태·호출 상한·명시한 설정을 확인하세요.', file=sys.stderr)
        return 2
    # 실행 전에 출력 파일과 커밋 정보를 확보한다. 준비 실패로 호출 결과를 잃지 않도록 한다.
    try:
        revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                  capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT,
                                   capture_output=True, text=True, check=True).stdout)
        source = ROOT / 'src/solo_leveling/infrastructure/generation/semantic_verifier.py'
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        target = ROOT / 'runtime/semantic-evaluation'
        target.mkdir(parents=True, exist_ok=True)
        output = target / f'provider-{uuid4().hex}.json'
        with output.open('x', encoding='utf-8') as handle:
            result = run_provider(selected, digest, values, repeats=args.repeats,
                                  max_calls=args.max_calls, execute=True)
            result.update(created_at=datetime.now(timezone.utc).isoformat(), code_revision=revision,
                          working_tree_dirty=dirty, selection_split=args.split,
                          include_drafts=args.include_drafts)
            # 원문과 키 대신 실행한 어댑터 소스의 해시를 기록한다.
            result['provider_source_sha256'] = source_hash
            json.dump(result, handle, ensure_ascii=False, indent=2)
    except (ValueError, OSError, subprocess.SubprocessError):
        print('평가 실행 또는 저장 실패. 자동 재시도하지 않습니다.', file=sys.stderr)
        return 2
    print(f'평가 결과: {output}')
    return 1 if result['summary']['failures'] else 0


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
