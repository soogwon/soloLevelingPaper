"""명시적인 로컬 설정으로 Desktop 연결 정보를 만들고 서버를 실행한다."""

import argparse
import json
import os
from pathlib import Path
import sys


def checked_environment(env_file: Path):
    """새 실행 경로에서만 절대 경로를 요구하며 기존 런타임 기본값은 보존한다."""
    from solo_leveling.interfaces.mcp.runtime import load_environment, runtime_paths

    if not env_file.is_absolute() or not env_file.is_file():
        raise ValueError('DESKTOP_ENV_FILE_REQUIRED')
    values = load_environment(env_file)
    if not Path(values.get('DATA_ROOT', '')).is_absolute():
        raise ValueError('DESKTOP_ABSOLUTE_DATA_ROOT_REQUIRED')
    paths = runtime_paths(values)
    for name in ('HF_HOME', 'HF_HUB_CACHE'):
        if name in values and not Path(values[name]).is_absolute():
            raise ValueError('DESKTOP_ABSOLUTE_CACHE_PATH_REQUIRED')
    return values, paths


def desktop_config(env_file: Path):
    """호스트 파일을 수정하지 않고 병합할 설정 조각만 반환한다."""
    return {'mcpServers': {'solo-leveling-paper': {
        'command': str(Path(sys.executable).resolve()),
        'args': ['-X', 'utf8', '-m', 'solo_leveling.interfaces.cli.desktop',
                 'serve', '--env-file', str(env_file)],
    }}}


def main(argv=None):
    parser = argparse.ArgumentParser(description='로컬 Claude Desktop 연결·설정 점검')
    parser.add_argument('action', choices=('config', 'check', 'serve', 'prepare-model'))
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--offline', action='store_true', help='모델 준비 시 로컬 캐시만 사용')
    parser.add_argument('--collect-raw-logs', action='store_true',
                        help='serve 전용: 민감정보가 포함될 수 있는 원본 진단 로그 수집')
    parser.add_argument('--detailed-diagnostics', action='store_true', help='serve 전용: 본문 없는 상세 검사 코드 기록')
    args = parser.parse_args(argv)
    if args.detailed_diagnostics and args.action != 'serve':
        parser.error('--detailed-diagnostics는 serve에서만 사용합니다.')
    if args.offline and args.action != 'prepare-model':
        parser.error('--offline은 prepare-model에서만 사용합니다.')
    if args.collect_raw_logs and args.action != 'serve':
        parser.error('--collect-raw-logs는 serve에서만 사용합니다.')
    try:
        values, paths = checked_environment(args.env_file)
        if args.action == 'config':
            print(json.dumps(desktop_config(args.env_file), ensure_ascii=False, indent=2))
            return 0
        from solo_leveling.interfaces.mcp.runtime import validate_runtime_settings
        if args.action != 'prepare-model':
            validate_runtime_settings(values)
        if args.action == 'check':
            # DB 생성·API 호출·모델 다운로드 없이 설치와 설정만 검사한다.
            import chromadb
            import mcp
            import pdfplumber
            from importlib.resources import files
            from importlib.util import find_spec
            if find_spec('sentence_transformers') is None:
                raise ImportError('모델 패키지 누락')
            asset = files('solo_leveling.interfaces.mcp').joinpath('assets/paper-status.html')
            if not asset.is_file():
                raise ValueError('UI 리소스 누락')
            print('설정·패키지 점검 완료. 모델 로딩·API 인증·Desktop 연결은 별도 확인이 필요합니다.')
            return 0
        os.environ['SOLO_LEVELING_ENV_FILE'] = str(args.env_file)
        for name in ('HF_HOME', 'HF_HUB_CACHE'):
            if name in values:
                os.environ[name] = values[name]
        if args.action == 'prepare-model':
            from .prepare_embedding_model import main as prepare
            return prepare(['--offline'] if args.offline else [])
        from .mcp_diagnostics import main as serve
        return serve(['--log-dir', str(paths.data_root / 'diagnostics'),
                      *(['--collect-raw-logs'] if args.collect_raw_logs else []),
                      *(['--detailed-diagnostics'] if args.detailed_diagnostics else [])])
    except Exception:
        # 입력값·키·원본 예외를 호스트 오류나 stdout에 노출하지 않는다.
        print('DESKTOP_SETUP_FAILED: 설정 파일·절대 경로·필수 설정·설치 의존성을 확인하세요.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
