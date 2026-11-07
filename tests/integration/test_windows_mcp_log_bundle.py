"""Windows 실제 MCP stdio 실행의 잠금과 종료 기록을 검증한다."""
import json
import os
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Thread

import pytest


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows MCP 로그')


@pytest.mark.parametrize('kind', ['safe', 'detail'])
def test_windows_server_lock_and_restart(tmp_path, kind):
    from solo_leveling.infrastructure.log_retention.policy import parse_log_name
    from solo_leveling.infrastructure.log_retention.windows import WindowsBundleStore

    root = tmp_path / 'logs'
    args = [sys.executable, '-X', 'utf8', '-m', 'solo_leveling.interfaces.cli.mcp_diagnostics',
            '--log-dir', str(root)]
    if kind == 'detail':
        args.append('--detailed-diagnostics')
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src'),
           'DATA_ROOT': str(tmp_path / 'data'), 'LOCAL_ONLY': 'true', 'ALLOW_EXTERNAL_API': 'false',
           'OPENAI_API_KEY': 'test-only'}
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}}
    store = WindowsBundleStore()
    for interrupted in (True, False):
        existing = set(root.glob('*.log'))
        with subprocess.Popen(args, cwd=tmp_path, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as server:
            try:
                line = Queue()
                reader = Thread(target=lambda: line.put(server.stdout.readline()), daemon=True)
                reader.start()
                server.stdin.write(json.dumps(request).encode() + b'\n')
                server.stdin.flush()
                assert 'serverInfo' in json.loads(line.get(timeout=30))['result']
                reader.join()
                log, = set(root.glob('*.log')) - existing
                bundle, actual_kind = parse_log_name(log.name)
                assert actual_kind == kind
                assert store.try_acquire_inspector(root, bundle).status == 'busy'
                assert not list(root.glob('*.closed.json'))
                if interrupted:
                    server.kill()
                stdout, stderr = server.communicate(timeout=25)
                if not interrupted:
                    assert server.returncode == 0 and stdout == stderr == b''
            finally:
                if server.poll() is None:
                    server.kill()
                server.wait(timeout=10)
        lease = store.try_acquire_inspector(root, bundle)
        assert lease.status == 'acquired'
        lease.lease.release()
        if interrupted:
            assert not list(root.glob('*.closed.json'))
            original, contents = log, log.read_bytes()
    metadata, = root.glob('*.closed.json')
    record = json.loads(metadata.read_bytes())
    entry, = record['files']
    assert record['run_id'] == bundle.run_id and entry['name'] == log.name
    info = log.stat()
    assert entry['identity'] == dict(platform='windows', st_dev=info.st_dev, st_ino=info.st_ino,
                                     st_size=info.st_size)
    assert original.read_bytes() == contents


def test_junction_ancestor_cannot_create_bundle(tmp_path):
    from solo_leveling.infrastructure.log_retention.contracts import BundleId
    from solo_leveling.infrastructure.log_retention.windows import WindowsBundleStore

    target, junction = tmp_path / 'target', tmp_path / 'junction'
    target.mkdir()
    subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(junction), str(target)],
                   check=True, capture_output=True, timeout=10)
    try:
        result = WindowsBundleStore().try_acquire_writer(junction / 'logs', BundleId('mcp', 'a' * 32))
        assert result.lease is None and not list(target.iterdir())
    finally:
        junction.rmdir()


def test_existing_close_record_survives_publish_failure(tmp_path):
    from datetime import datetime, timezone
    from solo_leveling.infrastructure.log_retention.contracts import BundleId, ClosedFile
    from solo_leveling.infrastructure.log_retention.policy import closed_name
    from solo_leveling.infrastructure.log_retention.windows import WindowsBundleStore

    store = WindowsBundleStore()
    bundle = BundleId('mcp', 'a' * 32)
    lease = store.try_acquire_writer(tmp_path, bundle).lease
    try:
        path, output = lease.open_log('safe')
        with output:
            output.write('safe')
        metadata = tmp_path / closed_name(bundle)
        metadata.write_bytes(b'existing')
        result = store.publish_closed(lease, (ClosedFile('safe', path.name, path.stat().st_size),),
                                      datetime.now(timezone.utc))
        assert result != 'published' and metadata.read_bytes() == b'existing'
        assert not list(tmp_path.glob('*.tmp'))
    finally:
        lease.release()
