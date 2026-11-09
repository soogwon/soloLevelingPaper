"""Windows 지연 로그 게시와 실제 합성 worker 종료를 검증한다. 삭제는 하지 않는다."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.integration.test_mcp_log_bundle import PROGRAM as POSIX_CLEANUP_PROBE


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows 로그 수명 검증')


def environment():
    # 개발자 설정·비밀 키를 합성 프로세스로 전달하지 않는다.
    env = {key: value for key, value in os.environ.items()
           if not any(part in key.upper() for part in ('OPENAI', 'API_KEY', 'SOLO_LEVELING'))
           and key not in ('DATA_ROOT', 'ENV_FILE')}
    package = os.environ.get('SOLO_LOG_TEST_INSTALLED_ROOT') or str(Path(__file__).resolve().parents[2] / 'src')
    env.update(PYTHONPATH=package, SYNTHETIC_EXPECTED_PACKAGE_ROOT=package,
               LOCAL_ONLY='true', ALLOW_EXTERNAL_API='false')
    return env


@pytest.mark.parametrize('mode', [
    'safe', 'detail', 'busy', 'publish_failed', 'disk_full', 'mkdir_failed', 'release_failed',
])
def test_windows_deferred_close_and_failure_preserve_stdout(tmp_path, mode):
    # POSIX의 동일한 종료 순서·민감 출력 검사를 Windows 구현에 그대로 적용한다.
    program = POSIX_CLEANUP_PROBE.replace(
        'solo_leveling.infrastructure.log_retention.posix',
        'solo_leveling.infrastructure.log_retention.windows',
    ).replace('PosixBundleStore', 'WindowsBundleStore').replace('PosixLease', 'WindowsLease')
    program = "import os, pathlib, solo_leveling\nassert pathlib.Path(solo_leveling.__file__).resolve().is_relative_to(pathlib.Path(os.environ['SYNTHETIC_EXPECTED_PACKAGE_ROOT']).resolve())\n" + program
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', program, mode, str(tmp_path)],
                            cwd=tmp_path, env=environment(), capture_output=True, timeout=25)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert result.stdout.strip() == b'MCP_STDOUT_UNCHANGED'
    assert result.stderr == b''
    records = list(tmp_path.glob('*.closed.json'))
    if mode in ('safe', 'detail', 'release_failed'):
        record, = records
        row = json.loads(record.read_bytes())
        item, = row['files']
        log = tmp_path / item['name']
        info = log.stat()
        assert item['kind'] == ('detail' if mode == 'detail' else 'safe')
        assert item['identity'] == dict(platform='windows', st_dev=info.st_dev,
                                        st_ino=info.st_ino, st_size=info.st_size)
    else:
        assert records == []
    for path in tmp_path.glob('*.log'):
        assert b'SYNTHETIC_PRIVATE' not in path.read_bytes()
    assert not list(tmp_path.glob('*.tmp'))
    if mode == 'busy':
        assert list(tmp_path.iterdir()) == []


WORKER_PROBE = r'''
import ctypes, faulthandler, json, os, sys, time
from pathlib import Path
import solo_leveling
from solo_leveling.infrastructure.embeddings import worker as diagnostic
from solo_leveling.infrastructure.embeddings.process_embedder import ProcessEmbedder, EmbeddingProcessError
assert Path(solo_leveling.__file__).resolve().is_relative_to(Path(os.environ['SYNTHETIC_EXPECTED_PACKAGE_ROOT']).resolve())

def worker(connection):
    request = connection.recv()
    folder = Path(request['texts'][0])
    def event(value):
        with (folder / 'events.jsonl').open('a', encoding='utf-8') as out:
            out.write(json.dumps({'event': value}) + '\n')
    event('entered')
    if diagnostic._stack_output is not None:
        cancel = faulthandler.cancel_dump_traceback_later
        def checked_cancel():
            # 실제 watchdog 취소가 호출되며, 그 뒤 스택 스트림을 닫아야 한다.
            assert not diagnostic._stack_output.closed
            cancel()
            event('watchdog_cancelled')
        faulthandler.cancel_dump_traceback_later = checked_cancel
    print('SYNTHETIC_PRIVATE', file=sys.stderr, flush=True)
    os.write(1, b'SYNTHETIC_NATIVE_PRIVATE\n')
    os.write(2, b'SYNTHETIC_NATIVE_PRIVATE\n')
    if request['model'] == 'hang':
        time.sleep(60)
    if request['model'] == 'fail':
        event('raised')
        raise RuntimeError('SYNTHETIC_PRIVATE')
    connection.send({'ok': True, 'dimension': 1, 'vectors': [[1.0]]})
    event('returned')

if __name__ == '__main__':
    from solo_leveling.infrastructure.log_retention.windows import _fd
    from solo_leveling.interfaces.cli.log_inventory import inventory
    mode, raw, folder = sys.argv[1:4]
    raw, folder = raw == '1', Path(folder)
    logs = folder / 'logs'
    embedder = ProcessEmbedder(6, worker_target=worker, log_dir=logs, collect_raw_logs=raw)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    owned = []
    original_event = embedder._event
    def observed_event(event, **values):
        if event == 'worker_spawn_return':
            process, connection = embedder._process, embedder._connection
            # 별도 핸들로 PID 재사용을 막고, 내부 상태/종료 로그와 무관하게 실제 종료를 확인한다.
            handle = kernel.OpenProcess(0x100001, False, values['pid'])
            assert handle
            owned.append((process, connection, handle))
        original_event(event, **values)
    embedder._event = observed_event
    if sys.argv[4:] == ['skip_stop']:
        # 음성 대조군: 참조만 지우고 종료를 생략해도 테스트가 성공해서는 안 된다.
        def skipped_stop():
            embedder._process = embedder._connection = None
        embedder._stop = skipped_stop
    try:
        if mode == 'ok':
            assert embedder([str(folder)], model_name=mode) == [[1.0]]
            process = embedder._process
            process.join(timeout=5)
            assert not process.is_alive() and process.exitcode == 0
            assert not process._closed
            embedder.close()
            assert process._closed
        else:
            try:
                embedder([str(folder)], model_name=mode)
                raise AssertionError('expected failure')
            except EmbeddingProcessError as error:
                assert error.code.value == ('EMBEDDING_TIMEOUT' if mode == 'hang' else 'EMBEDDING_UNAVAILABLE')
        assert embedder._process is None and embedder._connection is None
        assert len(owned) == 1
        process, connection, handle = owned[0]
        assert kernel.WaitForSingleObject(handle, 0) == 0, 'WORKER_STILL_ALIVE'
        assert process._closed and connection.closed
    finally:
        try:
            embedder.close()
        finally:
            for process, connection, handle in owned:
                if kernel.WaitForSingleObject(handle, 0) == 258:
                    assert kernel.TerminateProcess(handle, 1)
                    assert kernel.WaitForSingleObject(handle, 5000) == 0
                connection.close()
                if not process._closed:
                    process.join(timeout=5)
                    process.close()
                kernel.CloseHandle(handle)
    events = [json.loads(line)['event'] for line in (folder / 'events.jsonl').read_text().splitlines()]
    assert events[0] == 'entered'
    if mode != 'hang':
        assert events[1] == ('returned' if mode == 'ok' else 'raised')
        assert ('watchdog_cancelled' in events) is raw
    else:
        # Windows 강제 종료에는 자식 finally 실행을 요구하지 않는다.
        assert events == ['entered']
    files = list(logs.glob('*.log'))
    assert len(files) == (2 if raw else 1)
    before = {path: path.read_bytes() for path in files}
    for path in files:
        # 부모/자식에 로그 핸들이 남으면 삭제 공유 없는 배타 열기가 실패한다.
        fd = _fd(path, 0x80000000, os.O_RDONLY, share=0)
        os.close(fd)
    time.sleep(.1)
    assert {path: path.read_bytes() for path in files} == before
    text = b''.join(before.values())
    assert (b'SYNTHETIC_PRIVATE' in text) is raw
    assert (b'SYNTHETIC_NATIVE_PRIVATE' in text) is raw
    if raw and mode == 'hang':
        assert b'Timeout' in text
    closed, = logs.glob('slp-log-v1-worker-*.closed.json')
    record = json.loads(closed.read_text(encoding='utf-8'))
    assert record['owner'] == 'worker'
    assert record['run_id'] in closed.name
    assert {item['kind'] for item in record['files']} == ({'raw', 'stack'} if raw else {'safe'})
    assert {item['name'] for item in record['files']} == {path.name for path in files}
    assert all(item['size_bytes'] == (logs / item['name']).stat().st_size for item in record['files'])
    summary = inventory(logs)
    assert summary['file_count'] == len(files)
    assert all(row['activity'] == 'unknown' for row in summary['files'])
    assert {path: path.read_bytes() for path in files} == before
    print('WORKER_LIFECYCLE_OK', flush=True)
'''


@pytest.mark.parametrize('mode', ['ok', 'fail', 'hang'])
@pytest.mark.parametrize('raw', [False, True])
def test_windows_worker_exit_handles_watchdog_and_inventory(tmp_path, mode, raw):
    script = tmp_path / 'worker_probe.py'
    script.write_text(WORKER_PROBE, encoding='utf-8')
    result = subprocess.run([sys.executable, '-X', 'utf8', str(script), mode,
                             str(int(raw)), str(tmp_path)], cwd=tmp_path, env=environment(),
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert result.stdout.strip() == b'WORKER_LIFECYCLE_OK'
    assert b'SYNTHETIC_PRIVATE' not in result.stderr
    assert b'SYNTHETIC_NATIVE_PRIVATE' not in result.stderr
    events = [json.loads(line) for line in result.stderr.splitlines()]
    stopped = [event for event in events if event.get('event') == 'worker_stopped']
    assert len(stopped) == 1 and stopped[0]['alive'] is False
    assert not any(event.get('event') == 'worker_stop_failed' for event in events)


def test_worker_probe_detects_skipped_stop_even_after_references_are_cleared(tmp_path):
    script = tmp_path / 'worker_probe.py'
    script.write_text(WORKER_PROBE, encoding='utf-8')
    result = subprocess.run([sys.executable, '-X', 'utf8', str(script), 'hang', '0',
                             str(tmp_path), 'skip_stop'], cwd=tmp_path, env=environment(),
                            capture_output=True, timeout=30)
    assert result.returncode != 0
    assert b'WORKER_STILL_ALIVE' in result.stderr
