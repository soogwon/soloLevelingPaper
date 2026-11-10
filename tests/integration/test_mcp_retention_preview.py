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


@pytest.mark.parametrize('owner', ['mcp', 'worker'])
def test_killed_writer_and_restart_are_distinct(tmp_path, store, capsys, owner):
    child = """
import os, sys
from pathlib import Path
from solo_leveling.infrastructure.log_retention.contracts import BundleId
if os.name == 'nt':
    from solo_leveling.infrastructure.log_retention.windows import WindowsBundleStore as Store
else:
    from solo_leveling.infrastructure.log_retention.posix import PosixBundleStore as Store
lease = Store().try_acquire_writer(Path(sys.argv[1]), BundleId(sys.argv[2], '1'*32)).lease
path, stream = lease.open_log('safe')
print('ready', flush=True)
sys.stdin.read()
"""
    source_root = Path(__file__).resolve().parents[2] / 'src'
    child_env = dict(os.environ, PYTHONPATH=str(source_root))
    with subprocess.Popen([sys.executable, '-c', child, str(tmp_path), owner], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, text=True, env=child_env, cwd=tmp_path) as process:
        try:
            assert process.stdout.readline().strip() == 'ready'
            name = log_name(BundleId(owner, '1' * 32), 'safe')
            assert preview(tmp_path, capsys)[name] == ('busy', 'lock_busy')
        finally:
            process.kill()
            process.wait(timeout=10)
    fresh = BundleId(owner, '2' * 32)
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


@pytest.mark.parametrize('owner', ['mcp', 'worker'])
def test_raw_added_after_scan_excludes_bundle(tmp_path, store, capsys, monkeypatch, owner):
    from solo_leveling.interfaces.cli import log_inventory
    bundle = BundleId(owner, 'e' * 32)
    publish(store, tmp_path, bundle, ['safe'], 31)
    inspect = log_inventory.inspect_bundle

    def add_then_inspect(*args):
        (tmp_path / log_name(bundle, 'raw')).write_bytes(b'PRIVATE')
        return inspect(*args)

    monkeypatch.setattr(log_inventory, 'inspect_bundle', add_then_inspect)
    assert preview(tmp_path, capsys)[log_name(bundle, 'safe')] == ('unknown', 'file_set_changed')


def test_worker_deadlines_and_raw_stack_exclusion(tmp_path, store, capsys):
    recent, old, raw = [BundleId('worker', c * 32) for c in 'abc']
    publish(store, tmp_path, recent, ['safe'], 8)
    publish(store, tmp_path, old, ['safe'], 31)
    publish(store, tmp_path, raw, ['raw', 'stack'], 40)
    before = snapshot(tmp_path)
    rows = preview(tmp_path, capsys)
    assert rows[log_name(recent, 'safe')] == ('closed', 'retained')
    assert rows[log_name(old, 'safe')] == ('closed', 'expired')
    assert rows[log_name(raw, 'raw')] == rows[log_name(raw, 'stack')] == ('closed', 'raw_excluded')
    assert preview(tmp_path, capsys, '--safe-days', '7')[log_name(recent, 'safe')] == ('closed', 'expired')
    assert snapshot(tmp_path) == before


def cleanup(root, bundle, capsys, token=None):
    from solo_leveling.interfaces.cli.log_cleanup import main as cleanup_main
    args = ['--log-dir', str(root), '--bundle', f'{bundle.owner}-{bundle.run_id}']
    if token is not None:
        args.extend(['--apply', token])
    assert cleanup_main(args) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize('owner,kinds', [('worker', ['safe']), ('mcp', ['safe', 'detail'])])
def test_cleanup_explicit_verified_bundle(tmp_path, store, capsys, owner, kinds):
    bundle = BundleId(owner, '1' * 32)
    publish(store, tmp_path, bundle, kinds, 31)
    before = snapshot(tmp_path)
    planned = cleanup(tmp_path, bundle, capsys)
    assert planned['status'] == 'expired' and snapshot(tmp_path) == before
    assert cleanup(tmp_path, bundle, capsys, planned['token'])['status'] == 'deleted'
    assert [p.suffix for p in tmp_path.iterdir()] == ['.lock']
    assert cleanup(tmp_path, bundle, capsys, planned['token'])['status'] == 'missing_closed_record'


@pytest.mark.parametrize('change,expected', [
    ('replace', 'file_changed'), ('lock', 'lock_busy'), ('raw', 'file_set_changed'),
    ('record', 'file_changed')])
def test_cleanup_rechecks_preview(tmp_path, store, capsys, change, expected):
    bundle = BundleId('worker', '2' * 32)
    publish(store, tmp_path, bundle, ['safe'], 31)
    token = cleanup(tmp_path, bundle, capsys)['token']
    if change == 'raw':
        (tmp_path / log_name(bundle, 'raw')).write_bytes(b'RAW')
    elif change in ('record', 'replace'):
        name = closed_name(bundle) if change == 'record' else log_name(bundle, 'safe')
        replacement = tmp_path / 'replacement'
        replacement.write_bytes((tmp_path / name).read_bytes())
        replacement.replace(tmp_path / name)
    before = snapshot(tmp_path)
    lease = None
    if change == 'lock':
        lease = store.try_acquire_inspector(tmp_path, bundle).lease
        assert lease is not None
    try:
        assert cleanup(tmp_path, bundle, capsys, token)['status'] == expected
    finally:
        if lease:
            lease.release()
    assert snapshot(tmp_path) == before


@pytest.mark.skipif(os.name != 'posix', reason='POSIX 이름 교체 경합')
def test_cleanup_swap_at_last_rename_preserves_replacement(tmp_path, store, capsys, monkeypatch):
    bundle = BundleId('worker', '3' * 32)
    publish(store, tmp_path, bundle, ['safe'], 31)
    token = cleanup(tmp_path, bundle, capsys)['token']
    name = log_name(bundle, 'safe')
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(b'CHANGED')
    rename = os.rename

    def swap(src, dst, **kwargs):
        if src == name:
            replacement.replace(tmp_path / name)
        rename(src, dst, **kwargs)

    monkeypatch.setattr(os, 'rename', swap)
    assert cleanup(tmp_path, bundle, capsys, token)['status'] == 'file_changed'
    assert (tmp_path / name).read_bytes() == b'CHANGED'
    assert (tmp_path / closed_name(bundle)).exists()


@pytest.mark.parametrize('kinds,days,status', [
    (['safe', 'raw'], 60, 'raw_excluded'), (['safe'], 5, 'retained')])
def test_apply_cannot_override_retention(tmp_path, store, capsys, kinds, days, status):
    bundle = BundleId('mcp', 'a' * 32)
    publish(store, tmp_path, bundle, kinds, days)
    before = snapshot(tmp_path)
    assert cleanup(tmp_path, bundle, capsys, '0' * 64)['status'] == status
    assert before == snapshot(tmp_path)


def test_partial_cleanup_requires_fresh_preview_for_remaining_files(tmp_path, store, capsys):
    bundle = BundleId('mcp', 'a' * 32)
    publish(store, tmp_path, bundle, ['safe', 'detail'], 31)
    old_token = cleanup(tmp_path, bundle, capsys)['token']
    (tmp_path / log_name(bundle, 'safe')).unlink()
    assert cleanup(tmp_path, bundle, capsys, old_token)['status'] == 'file_changed'
    assert (tmp_path / log_name(bundle, 'detail')).exists()
    fresh_token = cleanup(tmp_path, bundle, capsys)['token']
    assert fresh_token != old_token
    assert cleanup(tmp_path, bundle, capsys, fresh_token)['status'] == 'deleted'


def test_cleanup_skips_log_locked_by_another_process(tmp_path, store, capsys):
    bundle = BundleId('worker', 'f' * 32)
    publish(store, tmp_path, bundle, ['safe'], 31)
    token = cleanup(tmp_path, bundle, capsys)['token']
    child = """import os, sys
with open(sys.argv[1]) as stream:
    if os.name == "posix":
        import fcntl
        fcntl.flock(stream, fcntl.LOCK_SH)
    print('ready', flush=True)
    sys.stdin.read()
"""
    with subprocess.Popen([sys.executable, '-c', child, str(tmp_path / log_name(bundle, 'safe'))],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as proc:
        try:
            assert proc.stdout.readline().strip() == 'ready'
            before = snapshot(tmp_path)
            assert cleanup(tmp_path, bundle, capsys, token)['status'] == 'lock_busy'
            assert snapshot(tmp_path) == before
        finally:
            proc.communicate('', timeout=5)
