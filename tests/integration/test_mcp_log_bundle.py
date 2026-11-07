"""실제 stderr 복원과 지연 종료 뒤 게시, 실패 시 MCP 출력 보존을 확인한다."""
import json
import os
from pathlib import Path
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
