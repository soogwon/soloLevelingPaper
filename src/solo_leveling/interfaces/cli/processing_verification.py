"""격리 검증 설정을 검사하고 명시적으로 승인된 단발 worker만 실행한다."""
from argparse import ArgumentParser
from contextlib import redirect_stdout, redirect_stderr
import importlib
import json
import logging
import os
from pathlib import Path
import re

from solo_leveling.workers.verification import VerificationConfig, run_once


def _unique(items):
    output = {}
    for key, value in items:
        if key in output: raise ValueError('verification_config_invalid')
        output[key] = value
    return output


def load_config(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(16_385)
    if len(raw) > 16_384: raise ValueError('verification_config_invalid')
    return VerificationConfig(**json.loads(raw, object_pairs_hook=_unique))


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='비밀정보를 포함하지 않는 검증 설정 JSON')
    parser.add_argument('--execute', action='store_true', help='승인한 외부 전송이 발생할 수 있는 단발 실행')
    parser.add_argument('--runtime', help='운영자가 검토한 Python module:factory (신뢰하는 서버 코드)')
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if not args.execute:
            print(json.dumps({'status': 'config_valid', 'executed': False}))
            return 0
        if not args.runtime or not re.fullmatch(r'[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*:[a-zA-Z_]\w*', args.runtime):
            raise ValueError('verification_runtime_required')
        # DSN·원문·provider 예외가 표준 진단으로 새지 않도록 내부 출력은 폐기한다.
        previous = logging.root.manager.disable
        try:
            logging.disable(logging.CRITICAL)
            with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
                module, name = args.runtime.split(':')
                factory = getattr(importlib.import_module(module), name)
                with factory(config) as resources:
                    status = run_once(config, resources)
        finally:
            logging.disable(previous)
    except (Exception, KeyboardInterrupt):
        print(json.dumps({'status': 'verification_failed'}))
        return 1
    print(json.dumps({'status': status}))
    return 0 if status == 'published' else 3


if __name__ == '__main__':
    raise SystemExit(main())
