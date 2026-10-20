"""MCP 통신은 그대로 두고 stderr만 실행별 파일에 저장한다."""

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
from uuid import uuid4

def preload_embedding_library():
    """서버 시작 전 메인 스레드에서 import만 수행하고 모델은 만들지 않는다."""
    from solo_leveling.diagnostics import traced, import_watchdog

    @traced('startup_embedding_import', request=True)
    def load():
        with import_watchdog():
            from sentence_transformers import SentenceTransformer

    load()


def main(argv=None):
    parser = argparse.ArgumentParser(description='MCP stderr 파일 수집 실행기')
    parser.add_argument('--log-dir', type=Path)
    parser.add_argument('--check', action='store_true', help='출력 경로·모듈 경로만 검사하고 서버는 시작하지 않음')
    parser.add_argument('--preload-embedding-library', action='store_true',
                        help='메인 스레드 선행 import 비교 진단; 모델 생성 없음')
    args = parser.parse_args(argv)
    if args.log_dir is None:
        from solo_leveling.interfaces.mcp.runtime import load_environment, runtime_paths
        args.log_dir = runtime_paths(load_environment()).data_root / 'diagnostics'
    try:
        args.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        path = args.log_dir.resolve() / f'mcp-{stamp}-{uuid4().hex}.log'
        # 파일 설명자도 바꿔 Python 외 라이브러리의 stderr 출력을 함께 수집한다.
        with path.open('x', encoding='utf-8') as output:
            sys.stderr.flush()
            os.dup2(output.fileno(), 2)
        sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)
    except (OSError, AttributeError):
        print('MCP_DIAGNOSTIC_LOG_SETUP_FAILED', file=sys.stderr)
        return 2

    try:
        from solo_leveling.diagnostics import configure_stack_diagnostics
        configure_stack_diagnostics(args.log_dir.resolve())
        modules = {}
        for name in ('solo_leveling.diagnostics', 'solo_leveling.interfaces.mcp.server'):
            spec = importlib.util.find_spec(name)
            if spec is None or spec.origin is None:
                raise RuntimeError('모듈 확인 실패')
            modules[name] = spec.origin
        print(json.dumps({'event': 'diagnostic_startup', 'pid': os.getpid(),
                          'python': sys.executable, 'modules': modules,
                          'log_file': str(path), 'check_only': args.check,
                          'preload_embedding_library': args.preload_embedding_library}), file=sys.stderr, flush=True)
        if args.check:
            from solo_leveling.diagnostics import traced

            @traced('diagnostic_probe', request=True)
            def probe():
                pass

            probe()
            return 0
        if args.preload_embedding_library:
            preload_embedding_library()
        runpy.run_module('solo_leveling.interfaces.mcp', run_name='__main__')
        return 0
    except Exception:
        print('MCP_DIAGNOSTIC_STARTUP_FAILED', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
