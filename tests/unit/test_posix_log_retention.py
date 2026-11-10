"""임시 파일과 독립 프로세스로 잠금·경로·종료 게시 경계를 확인한다."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from solo_leveling.infrastructure.log_retention.contracts import BundleId, ClosedFile
from solo_leveling.infrastructure.log_retention.posix import PosixBundleStore
from solo_leveling.infrastructure.log_retention.policy import closed_name, parse_metadata_summary

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX 실제 파일 잠금 검증')
NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


@pytest.fixture
def owned(tmp_path):
    store = PosixBundleStore()
    bundle = BundleId('mcp', 'a' * 32)
    lease = store.try_acquire_writer(tmp_path.resolve(), bundle).lease
    assert lease is not None
    yield store, bundle, lease
    for _, _, stream in lease.files.values():
        stream.close()
    lease.release()


def closed_file(lease):
    path, output = lease.open_log('safe')
    output.write('합성 로그\n')
    output.close()
    return path, ClosedFile('safe', path.name, path.stat().st_size)


def test_publish_only_after_close_and_preserve_metadata(owned):
    store, bundle, lease = owned
    path, output = lease.open_log('safe')
    output.write('합성 로그\n')
    output.flush()
    item = ClosedFile('safe', path.name, path.stat().st_size)
    assert store.publish_closed(lease, (item,), NOW) == 'invalid_state'
    output.close()
    assert store.publish_closed(lease, (item,), NOW) == 'published'
    metadata = lease.root / closed_name(bundle)
    before = metadata.read_bytes()
    assert metadata.stat().st_nlink == 1
    assert parse_metadata_summary(before, expected=bundle).files == (item,)
    assert json.loads(before)['files'][0]['identity']['st_ino'] == path.stat().st_ino
    assert store.publish_closed(lease, (item,), NOW) == 'invalid_state'
    assert metadata.read_bytes() == before
    with pytest.raises(ValueError):
        lease.open_log('detail')


def test_inspector_does_not_create_and_cannot_publish(tmp_path):
    store = PosixBundleStore()
    bundle = BundleId('mcp', 'b' * 32)
    assert store.try_acquire_inspector(tmp_path.resolve(), bundle).status == 'missing'
    assert list(tmp_path.iterdir()) == []
    lease = store.try_acquire_writer(tmp_path.resolve(), bundle).lease
    assert store.try_acquire_inspector(tmp_path.resolve(), bundle).status == 'busy'
    lease.release()
    assert store.try_acquire_writer(tmp_path.resolve(), bundle).status == 'busy'
    inspector = store.try_acquire_inspector(tmp_path.resolve(), bundle).lease
    try:
        assert store.publish_closed(inspector, (), NOW) == 'invalid_state'
    finally:
        inspector.release()


@pytest.mark.parametrize('replacement', ['symlink', 'hardlink', 'different_file'])
def test_changed_log_is_not_published(owned, tmp_path, replacement):
    store, bundle, lease = owned
    path, item = closed_file(lease)
    outside = tmp_path / 'private'
    outside.write_bytes(b'x' * item.size_bytes)
    path.unlink()
    if replacement == 'symlink':
        path.symlink_to(outside)
    elif replacement == 'hardlink':
        os.link(outside, path)
    else:
        path.write_bytes(b'x' * item.size_bytes)
    assert store.publish_closed(lease, (item,), NOW) in ('invalid_state', 'io_error')
    assert not (tmp_path / closed_name(bundle)).exists()


def test_lock_replacement_invalidates_lease(owned):
    store, bundle, lease = owned
    _, item = closed_file(lease)
    lock = lease.root / lease.lock_name
    lock.rename(lease.root / 'old-lock')
    lock.touch()
    assert not lease.valid()
    assert store.publish_closed(lease, (item,), NOW) == 'invalid_state'


def test_parent_symlink_is_rejected_without_creation(tmp_path):
    actual = tmp_path / 'actual'
    actual.mkdir()
    link = tmp_path / 'alias'
    link.symlink_to(actual, target_is_directory=True)
    result = PosixBundleStore().try_acquire_writer(link, BundleId('mcp', 'c' * 32))
    assert result.lease is None
    assert list(actual.iterdir()) == []


def test_publish_io_failure_preserves_unknown_state(owned, monkeypatch):
    store, bundle, lease = owned
    _, item = closed_file(lease)
    def fail(*args, **kwargs):
        raise OSError('합성 게시 실패')
    monkeypatch.setattr(os, 'link', fail)
    assert store.publish_closed(lease, (item,), NOW) == 'io_error'
    assert not (lease.root / closed_name(bundle)).exists()
    assert list(lease.root.glob('.slp-closed-*')) == []


def test_existing_closed_file_is_never_overwritten(owned):
    store, bundle, lease = owned
    _, item = closed_file(lease)
    target = lease.root / closed_name(bundle)
    target.write_bytes(b'previous')
    assert store.publish_closed(lease, (item,), NOW) == 'invalid_state'
    assert target.read_bytes() == b'previous'


def test_other_process_cannot_acquire_active_bundle(owned):
    _, bundle, lease = owned
    program = '''
import sys
from pathlib import Path
from solo_leveling.infrastructure.log_retention.contracts import BundleId
from solo_leveling.infrastructure.log_retention.posix import PosixBundleStore
result = PosixBundleStore().try_acquire_inspector(Path(sys.argv[1]), BundleId('mcp', sys.argv[2]))
print(result.status)
if result.lease:
    result.lease.release()
'''
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, 'PYTHONPATH': str(root / 'src')}
    active = subprocess.run([sys.executable, '-c', program, str(lease.root), bundle.run_id],
                            env=env, capture_output=True, text=True, timeout=10, check=True)
    assert active.stdout.strip() == 'busy'
    lease.release()
    closed = subprocess.run([sys.executable, '-c', program, str(lease.root), bundle.run_id],
                            env=env, capture_output=True, text=True, timeout=10, check=True)
    assert closed.stdout.strip() == 'acquired'


def test_fork_invalidates_parent_publication_and_releases_child_lock_handles(tmp_path):
    program = '''
from pathlib import Path
import os, sys
from solo_leveling.infrastructure.log_retention.contracts import BundleId
from solo_leveling.infrastructure.log_retention.posix import PosixBundleStore
store = PosixBundleStore()
lease = store.try_acquire_writer(Path(sys.argv[1]), BundleId('mcp', 'd' * 32)).lease
pid = os.fork()
if pid == 0:
    os._exit(0 if lease.root_fd is None and lease.lock_fd is None else 1)
_, status = os.waitpid(pid, 0)
assert os.waitstatus_to_exitcode(status) == 0
assert not lease.valid()
lease.release()
'''
    root = Path(__file__).resolve().parents[2]
    subprocess.run([sys.executable, '-c', program, str(tmp_path.resolve())],
                   env={**os.environ, 'PYTHONPATH': str(root / 'src')},
                   capture_output=True, text=True, timeout=10, check=True)


def test_failed_temporary_unlink_does_not_become_valid_closed_metadata(owned, monkeypatch):
    store, bundle, lease = owned
    _, item = closed_file(lease)
    original_unlink = os.unlink
    calls = []

    def fail_first_unlink(path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            raise OSError('합성 임시 링크 정리 실패')
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, 'unlink', fail_first_unlink)
    assert store.publish_closed(lease, (item,), NOW) == 'io_error'
    assert (lease.root / closed_name(bundle)).stat().st_nlink == 2
    assert len(calls) == 1


def test_writer_creates_missing_root_but_inspector_does_not(tmp_path):
    root = tmp_path.resolve() / 'missing' / 'logs'
    store = PosixBundleStore()
    bundle = BundleId('mcp', 'e' * 32)
    assert store.try_acquire_inspector(root, bundle).status == 'missing'
    assert not root.parent.exists()
    result = store.try_acquire_writer(root, bundle)
    assert result.status == 'acquired'
    result.lease.release()


def test_root_creation_does_not_follow_parent_link(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(outside, target_is_directory=True)
    result = PosixBundleStore().try_acquire_writer(alias / 'logs', BundleId('mcp', 'e' * 32))
    assert result.lease is None
    assert list(outside.iterdir()) == []


def test_metadata_stream_creation_failure_closes_descriptor(owned, monkeypatch):
    store, bundle, lease = owned
    _, item = closed_file(lease)
    descriptors = []
    def fail_fdopen(fd, *args, **kwargs):
        descriptors.append(fd)
        raise OSError('합성 스트림 생성 실패')
    monkeypatch.setattr(os, 'fdopen', fail_fdopen)
    assert store.publish_closed(lease, (item,), NOW) == 'io_error'
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert not (lease.root / closed_name(bundle)).exists()
    assert list(lease.root.glob('.slp-closed-*')) == []


def test_release_attempts_both_handles_even_when_close_fails(owned, monkeypatch):
    _, _, lease = owned
    expected = {lease.lock_fd, lease.root_fd}
    calls = []
    original_close = os.close
    def fail_after_close(fd):
        original_close(fd)
        calls.append(fd)
        raise OSError('합성 핸들 정리 실패')
    monkeypatch.setattr(os, 'close', fail_after_close)
    with pytest.raises(OSError):
        lease.release()
    assert set(calls) == expected
    assert lease.lock_fd is None and lease.root_fd is None


@pytest.mark.parametrize('kind', ['safe', 'raw', 'stack'])
def test_worker_publication_waits_for_last_writer(tmp_path, kind):
    store = PosixBundleStore()
    bundle = BundleId('worker', 'a' * 32)
    lease = store.try_acquire_writer(tmp_path.resolve(), bundle).lease
    assert lease is not None
    try:
        path, output = lease.open_log(kind)
        with output, os.fdopen(os.dup(output.fileno()), 'wb') as duplicate:
            output.close()
            item = ClosedFile(kind, path.name, 0)
            assert store.publish_closed(lease, (item,), NOW) == 'invalid_state'
            assert not (lease.root / closed_name(bundle)).exists()
            duplicate.write(b'late')
        item = ClosedFile(kind, path.name, 4)
        assert store.publish_closed(lease, (item,), NOW) == 'published'
        assert parse_metadata_summary((lease.root / closed_name(bundle)).read_bytes(),
                                      expected=bundle).files == (item,)
    finally:
        lease.release()
