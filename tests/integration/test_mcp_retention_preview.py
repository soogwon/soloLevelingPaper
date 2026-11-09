"""운영 로그 없이 생산자·미리보기 CLI의 파일 수명 계약을 확인한다."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from solo_leveling.infrastructure.log_retention.contracts import BundleId, ClosedFile
from solo_leveling.infrastructure.log_retention.policy import closed_name, log_name
from solo_leveling.interfaces.cli.log_inventory import main


@pytest.fixture
def store():
    if os.name == 'nt':
        from solo_leveling.infrastructure.log_retention.windows import WindowsBundleStore
        return WindowsBundleStore()
    from solo_leveling.infrastructure.log_retention.posix import PosixBundleStore
    return PosixBundleStore()


def preview(root, capsys, *args):
    assert main(['--log-dir', str(root), '--retention', *args]) == 0
    output = capsys.readouterr().out
    assert 'PRIVATE' not in output
    return {row['name']: (row['activity'], row['retention_status']) for row in json.loads(output)['files']}


def publish(store, root, bundle, kinds, days):
    lease = store.try_acquire_writer(root, bundle).lease
    try:
        files = []
        for kind in kinds:
            path, stream = lease.open_log(kind)
            with stream:
                stream.write('PRIVATE')
            files.append(ClosedFile(kind, path.name, path.stat().st_size))
        assert store.publish_closed(lease, tuple(files), datetime.now(timezone.utc) - timedelta(days=days)) == 'published'
    finally:
        lease.release()


def snapshot(root):
    return {p.name: (p.read_bytes(), p.stat().st_size, p.stat().st_mtime_ns) for p in root.iterdir()}


def test_kind_deadlines_raw_exclusion_and_no_changes(tmp_path, store, capsys):
    safe, detail, mixed = [BundleId('mcp', c * 32) for c in 'abc']
    publish(store, tmp_path, safe, ['safe'], 8)
    publish(store, tmp_path, detail, ['detail'], 8)
    publish(store, tmp_path, mixed, ['safe', 'raw'], 40)
    before = snapshot(tmp_path)
    rows = preview(tmp_path, capsys)
    assert rows[log_name(safe, 'safe')] == ('closed', 'retained')
    assert rows[log_name(detail, 'detail')] == ('closed', 'expired')
    assert rows[log_name(mixed, 'safe')] == rows[log_name(mixed, 'raw')] == ('closed', 'raw_excluded')
    assert preview(tmp_path, capsys, '--safe-days', '7')[log_name(safe, 'safe')] == ('closed', 'expired')
    assert snapshot(tmp_path) == before


def test_same_size_replacement_and_invalid_identity(tmp_path, store, capsys):
    bundle = BundleId('mcp', 'c' * 32)
    name = log_name(bundle, 'safe')
    publish(store, tmp_path, bundle, ['safe'], 31)
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(b'changed')
    replacement.replace(tmp_path / name)
    assert preview(tmp_path, capsys)[name] == ('unknown', 'file_changed')
    path = tmp_path / closed_name(bundle)
    record = json.loads(path.read_bytes())
    record['files'][0]['identity'] = None
    path.write_text(json.dumps(record))
    assert preview(tmp_path, capsys)[name] == ('unknown', 'invalid_metadata')


def test_killed_writer_and_restart_are_distinct(tmp_path, store, capsys):
    child = """
import os, sys
from pathlib import Path
from solo_leveling.infrastructure.log_retention.contracts import BundleId
if os.name == 'nt':
    from solo_leveling.infrastructure.log_retention.windows import WindowsBundleStore as Store
else:
    from solo_leveling.infrastructure.log_retention.posix import PosixBundleStore as Store
lease = Store().try_acquire_writer(Path(sys.argv[1]), BundleId('mcp', '1'*32)).lease
path, stream = lease.open_log('safe')
print('ready', flush=True)
sys.stdin.read()
"""
    source_root = Path(__file__).resolve().parents[2] / 'src'
    child_env = dict(os.environ, PYTHONPATH=str(source_root))
    with subprocess.Popen([sys.executable, '-c', child, str(tmp_path)], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, text=True, env=child_env, cwd=tmp_path) as process:
        try:
            assert process.stdout.readline().strip() == 'ready'
            name = log_name(BundleId('mcp', '1' * 32), 'safe')
            assert preview(tmp_path, capsys)[name] == ('busy', 'lock_busy')
        finally:
            process.kill()
            process.wait(timeout=10)
    fresh = BundleId('mcp', '2' * 32)
    lease = store.try_acquire_writer(tmp_path, fresh).lease
    try:
        _, stream = lease.open_log('safe')
        with stream:
            rows = preview(tmp_path, capsys)
            assert rows[name] == ('unknown', 'missing_closed_record')
            assert rows[log_name(fresh, 'safe')] == ('busy', 'lock_busy')
    finally:
        lease.release()


@pytest.mark.parametrize('change', ['symlink', 'oversized', 'extra_log'])
def test_untrusted_record_or_incomplete_set_is_not_expired(tmp_path, store, capsys, change):
    bundle = BundleId('mcp', 'd' * 32)
    publish(store, tmp_path, bundle, ['detail'], 9)
    record = tmp_path / closed_name(bundle)
    if change == 'symlink':
        external = tmp_path.parent / (tmp_path.name + '-external.json')
        record.rename(external)
        try:
            record.symlink_to(external)
        except OSError:
            pytest.skip('심볼릭 링크 생성 권한 필요')
    elif change == 'oversized':
        record.write_bytes(b' ' * (16 * 1024 + 1))
    else:
        (tmp_path / log_name(bundle, 'safe')).write_bytes(b'PRIVATE')
    state, reason = preview(tmp_path, capsys)[log_name(bundle, 'detail')]
    assert state == 'unknown'
    assert reason in ('unsafe_file', 'invalid_metadata', 'file_set_changed')


def test_raw_added_after_scan_excludes_bundle(tmp_path, store, capsys, monkeypatch):
    from solo_leveling.interfaces.cli import log_inventory
    bundle = BundleId('mcp', 'e' * 32)
    publish(store, tmp_path, bundle, ['safe'], 31)
    inspect = log_inventory.inspect_mcp_bundle

    def add_then_inspect(*args):
        (tmp_path / log_name(bundle, 'raw')).write_bytes(b'PRIVATE')
        return inspect(*args)

    monkeypatch.setattr(log_inventory, 'inspect_mcp_bundle', add_then_inspect)
    assert preview(tmp_path, capsys)[log_name(bundle, 'safe')] == ('unknown', 'file_set_changed')
