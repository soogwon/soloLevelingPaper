"""실제 stderr 복원과 지연 종료 뒤 게시, 실패 시 MCP 출력 보존을 확인한다."""
import json
import os
from pathlib import Path
from contextlib import contextmanager
import select
import signal
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX MCP 로그 묶음 연결')

PROGRAM = r'''
import os, runpy, sys, threading, time
from pathlib import Path
from types import SimpleNamespace
from solo_leveling.interfaces.cli.mcp_diagnostics import main
from solo_leveling.interfaces.mcp.lifecycle import track_lifetime
from solo_leveling.infrastructure.log_retention.contracts import LeaseResult
from solo_leveling.infrastructure.log_retention.posix import PosixBundleStore
mode, folder = sys.argv[1:]
root = Path(folder)
if mode == 'mkdir_failed':
    root = root / 'unavailable'
previous = sys.stderr
previous_identity = os.fstat(2)
finished = threading.Event()
original_publish = PosixBundleStore.publish_closed

def publish(self, lease, files, now):
    assert finished.is_set()
    assert sys.stderr is previous
    assert os.fstat(2).st_ino == previous_identity.st_ino
    assert all(stream.closed for _, _, stream in lease.files.values())
    if mode == 'publish_failed':
        return 'io_error'
    return original_publish(self, lease, files, now)
PosixBundleStore.publish_closed = publish
if mode == 'busy':
    PosixBundleStore.try_acquire_writer = lambda *args: LeaseResult('busy')
if mode == 'mkdir_failed':
    def fail_mkdir(*args, **kwargs):
        raise OSError('SYNTHETIC_PRIVATE_MKDIR_ERROR')
    os.mkdir = fail_mkdir
if mode == 'release_failed':
    from solo_leveling.infrastructure.log_retention.posix import PosixLease
    original_release = PosixLease.release
    def fail_release(self):
        original_release(self)
        raise OSError('SYNTHETIC_PRIVATE_CLOSE_ERROR')
    PosixLease.release = fail_release
if mode == 'disk_full':
    def fail(*args):
        raise OSError('SYNTHETIC_PRIVATE_DISK_ERROR')
    os.fsync = fail

def server(*args, **kwargs):
    def finish():
        time.sleep(.15)
        assert not list(root.glob('*.closed.json'))
        print('SYNTHETIC_PRIVATE_TEXT', file=sys.stderr, flush=True)
        os.write(2, b'SYNTHETIC_PRIVATE_NATIVE\n')
        finished.set()
    track_lifetime(SimpleNamespace(begin=lambda: None, done=finished))
    threading.Thread(target=finish).start()
    print('MCP_STDOUT_UNCHANGED', flush=True)
runpy.run_module = server
result = main(['--log-dir', str(root), *(['--detailed-diagnostics'] if mode == 'detail' else [])])
assert finished.is_set()
assert sys.stderr is previous
raise SystemExit(result)
'''


@pytest.mark.parametrize('mode', ['safe', 'detail', 'busy', 'publish_failed', 'disk_full', 'mkdir_failed', 'release_failed'])
def test_deferred_cleanup_and_failures_preserve_request(tmp_path, mode):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, '-c', PROGRAM, mode, str(tmp_path.resolve())],
        env={**os.environ, 'PYTHONPATH': str(root / 'src')},
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'MCP_STDOUT_UNCHANGED'
    assert result.stderr == ''
    metadata = list(tmp_path.glob('*.closed.json'))
    if mode in ('safe', 'detail', 'release_failed'):
        assert len(metadata) == 1
        row = json.loads(metadata[0].read_text())
        assert row['files'][0]['kind'] == ('detail' if mode == 'detail' else 'safe')
        assert metadata[0].stat().st_nlink == 1
    else:
        assert metadata == []
    for path in tmp_path.glob('*.log'):
        assert 'SYNTHETIC_PRIVATE' not in path.read_text()
    if mode == 'busy':
        assert list(tmp_path.iterdir()) == []


def test_log_write_failure_is_discarded_without_propagation():
    from solo_leveling.interfaces.cli.mcp_diagnostics import _BestEffortLog
    class BrokenOutput:
        def write(self, text):
            raise OSError('합성 민감정보')
        def flush(self):
            raise AssertionError('실패 후 재시도하지 않아야 함')
    sink = _BestEffortLog(BrokenOutput())
    assert sink.write('test') == 4
    assert sink.failed
    sink.flush()
    assert sink.write('next') == 4


@contextmanager
def _initialized_server(args, environment, cwd):
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}}
    with subprocess.Popen(args, env=environment, cwd=cwd, text=True,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            process.stdin.write(json.dumps(request) + '\n')
            process.stdin.flush()
            assert select.select([process.stdout], [], [], 20)[0], 'MCP 초기화 응답 시간 초과'
            response = json.loads(process.stdout.readline())
            assert response['id'] == 1 and 'serverInfo' in response['result']
            yield process
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


@pytest.mark.parametrize('kind', ['safe', 'detail'])
def test_abrupt_exit_and_restart_keep_interrupted_bundle_unknown(tmp_path, kind):
    from solo_leveling.infrastructure.log_retention.policy import parse_log_name
    from solo_leveling.infrastructure.log_retention.posix import PosixBundleStore
    from solo_leveling.interfaces.cli.log_inventory import inventory

    root = tmp_path.resolve() / 'diagnostics'
    args = [sys.executable, '-m', 'solo_leveling.interfaces.cli.mcp_diagnostics', '--log-dir', str(root)]
    if kind == 'detail':
        args.append('--detailed-diagnostics')
    environment = {'PATH': os.environ['PATH'], 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src'),
                   'DATA_ROOT': str(tmp_path / 'data'), 'LOCAL_ONLY': 'true', 'ALLOW_EXTERNAL_API': 'false',
                   'OPENAI_API_KEY': 'test-only'}
    store = PosixBundleStore()
    with _initialized_server(args, environment, tmp_path) as first:
        original, = root.glob('*.log')
        bundle, original_kind = parse_log_name(original.name)
        assert original_kind == kind
        assert store.try_acquire_inspector(root, bundle).status == 'busy'
        first.kill()
        assert first.wait(timeout=10) == -signal.SIGKILL

    assert not list(root.glob('*.closed.json'))
    result = store.try_acquire_inspector(root, bundle)
    assert result.status == 'acquired'
    result.lease.release()
    before = original.read_bytes()

    with _initialized_server(args, environment, tmp_path) as second:
        stdout, stderr = second.communicate(timeout=20)
        assert second.returncode == 0 and not stdout and not stderr

    record, = root.glob('*.closed.json')
    row = json.loads(record.read_text(encoding='utf-8'))
    restarted, = set(root.glob('*.log')) - {original}
    assert row['run_id'] != bundle.run_id
    assert row['files'][0]['name'] == restarted.name
    assert original.read_bytes() == before
    assert next(item for item in inventory(root)['files'] if item['name'] == original.name)['activity'] == 'unknown'
