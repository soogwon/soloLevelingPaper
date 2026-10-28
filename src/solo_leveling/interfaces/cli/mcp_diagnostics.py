"""기본 진단은 최소 정보만 저장하고 원본 stderr 수집은 명시적으로 허용한다."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
from uuid import uuid4
from .safe_diagnostic_stream import SafeDiagnosticStream


@contextmanager
def diagnostic_stderr(output, *, raw, detailed=False):
    """원본 출력은 기본 폐기하고 종료 시 Python 스트림과 파일 설명자를 복원한다."""
    previous = sys.stderr
    previous.flush()
    saved_fd = os.dup(2)
    try:
        with open(os.devnull, 'w', encoding='utf-8') as discarded:
            target = output if raw else discarded
            os.dup2(target.fileno(), 2)
            sys.stderr = output if raw else SafeDiagnosticStream(output, detailed=detailed)
            from solo_leveling.diagnostics import diagnostic_verbosity
            with diagnostic_verbosity(detailed=detailed, raw=raw):
                yield
    finally:
        sys.stderr = previous
        os.dup2(saved_fd, 2)
        os.close(saved_fd)

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
    parser.add_argument('--collect-raw-logs', action='store_true',
                        help='민감정보가 포함될 수 있는 원본 stderr·경로·지연 스택 수집 허용')
    parser.add_argument('--detailed-diagnostics', action='store_true', help='본문 없이 정상 세부 검사 코드도 기록')
    args = parser.parse_args(argv)
    # 임베딩 자식 프로세스도 같은 선택을 따르게 한다. 서버를 다시 시작해야 바뀐다.
    if args.collect_raw_logs:
        os.environ['SOLO_LEVELING_COLLECT_RAW_LOGS'] = '1'
    else:
        os.environ.pop('SOLO_LEVELING_COLLECT_RAW_LOGS', None)
    try:
        if args.log_dir is None:
            from solo_leveling.interfaces.mcp.runtime import load_environment, runtime_paths
            args.log_dir = runtime_paths(load_environment()).data_root / 'diagnostics'
        args.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        path = args.log_dir.resolve() / f'mcp-{stamp}-{uuid4().hex}.log'
        with path.open('x', encoding='utf-8') as output:
            with diagnostic_stderr(output, raw=args.collect_raw_logs, detailed=args.detailed_diagnostics):
                return run_diagnostics(args, path, output)
    except Exception:
        print('MCP_DIAGNOSTIC_LOG_SETUP_FAILED', file=sys.stderr)
        return 2


def run_diagnostics(args, path, output):
    """고정 메타데이터는 직접 기록하고 상세 출력은 선택된 수집 정책을 따른다."""
    from solo_leveling import diagnostics
    previous_stack_directory = diagnostics._stack_directory
    try:
        diagnostics._stack_directory = None
        from solo_leveling.diagnostics import configure_stack_diagnostics
        if args.collect_raw_logs:
            configure_stack_diagnostics(args.log_dir.resolve())
        modules = {}
        for name in ('solo_leveling.diagnostics', 'solo_leveling.interfaces.mcp.server'):
            spec = importlib.util.find_spec(name)
            if spec is None or spec.origin is None:
                raise RuntimeError('모듈 확인 실패')
            modules[name] = spec.origin
        record = {'event': 'diagnostic_startup', 'pid': os.getpid(),
                  'check_only': args.check, 'raw_logs': args.collect_raw_logs,
                  'preload_embedding_library': args.preload_embedding_library}
        if args.collect_raw_logs:
            record.update(python=sys.executable, modules=modules, log_file=str(path))
        print(json.dumps(record), file=output, flush=True)
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
        print('MCP_DIAGNOSTIC_STARTUP_FAILED', file=output, flush=True)
        return 1
    finally:
        diagnostics._stack_directory = previous_stack_directory


if __name__ == '__main__':
    raise SystemExit(main())
