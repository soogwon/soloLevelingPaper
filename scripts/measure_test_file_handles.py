"""macOS·Linux의 별도 pytest 프로세스에서 파일 핸들 누적을 측정한다."""

import argparse
import importlib.metadata
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=4096)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--stop-chroma', action='store_true',
                        help='테스트 종료 후 이 프로세스의 Chroma 시스템만 종료해 비교')
    parser.add_argument('pytest_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if sys.platform not in ('darwin', 'linux'):
        parser.error('macOS 또는 Linux에서 실행해야 합니다.')

    import fcntl
    import resource
    import pytest

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if args.limit < 64 or (hard != resource.RLIM_INFINITY and args.limit > hard):
        parser.error('파일 수 제한은 64 이상이며 현재 하드 제한 이하여야 합니다.')

    def count_files():
        count = 0
        for fd in range(args.limit):
            try:
                fcntl.fcntl(fd, fcntl.F_GETFD)
                count += 1
            except OSError:
                pass
        return count

    def chroma_cache():
        module = sys.modules.get('chromadb.api.shared_system_client')
        return module.SharedSystemClient if module else None

    samples = []

    class Observer:
        def pytest_runtest_logreport(self, report):
            if report.when == 'teardown' and 'tests/integration/' in report.nodeid:
                cache = chroma_cache()
                samples.append({
                    'test': report.nodeid,
                    'fds': count_files(),
                    'systems': len(cache._identifier_to_system) if cache else 0,
                })

    pytest_args = args.pytest_args
    if pytest_args[:1] == ['--']:
        pytest_args = pytest_args[1:]
    pytest_args = pytest_args or ['tests', '-q', '--tb=short']
    # 파일 한도에 도달해도 결과를 쓸 수 있도록 미리 연다.
    with args.report.open('w', encoding='utf-8') as output:
        resource.setrlimit(resource.RLIMIT_NOFILE, (args.limit, hard))
        result = {
            'platform': sys.platform,
            'python': sys.version.split()[0],
            'chromadb': importlib.metadata.version('chromadb'),
            'original_soft_limit': soft,
            'limit': args.limit,
            'pytest_args': pytest_args,
            'samples': samples,
        }
        try:
            result['exit_code'] = int(pytest.main(pytest_args, plugins=[Observer()]))
        finally:
            result['fds_after_tests'] = count_files()
            cache = chroma_cache()
            result['systems_after_tests'] = len(cache._identifier_to_system) if cache else 0
            if args.stop_chroma and cache:
                # 내부 API를 사용하는 종료 실험은 테스트가 끝난 이 프로세스에만 적용한다.
                for system in list(cache._identifier_to_system.values()):
                    system.stop()
                cache.clear_system_cache()
                result['fds_after_stop'] = count_files()
            json.dump(result, output, ensure_ascii=False, indent=2)
            output.write('\n')
    return result['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
