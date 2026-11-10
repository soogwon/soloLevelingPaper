"""POSIX 로그 묶음의 비차단 잠금과 종료 게시. 삭제 권한은 제공하지 않는다."""
from contextlib import suppress
from datetime import datetime
import errno
import json
import os
from pathlib import Path
import stat
from uuid import uuid4
from weakref import WeakSet

from .contracts import BundleId, ClosedFile, LeaseResult
from .policy import closed_name, log_name, parse_metadata_summary, supported_log_kinds


_leases = WeakSet()


def _before_fork():
    # 추가 writer의 종료를 증명할 수 없는 fork 이후에는 종료 게시를 보류한다.
    for lease in tuple(_leases):
        lease.forked = True


def _after_fork():
    for lease in tuple(_leases):
        with suppress(OSError):
            lease.release()


if os.name == 'posix':
    os.register_at_fork(before=_before_fork, after_in_child=_after_fork)


def _identity(info):
    return info.st_dev, info.st_ino


def _regular(info):
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1


def _open_root(root, *, create=False):
    """상위 디렉터리까지 심볼릭 링크를 따라가지 않는다."""
    path = Path(os.path.abspath(root))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                with suppress(FileExistsError):
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return path, descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _exists(directory, name):
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


class PosixLease:
    def __init__(self, root, bundle_id, role, root_fd, lock_fd, lock_name):
        self.root, self.bundle_id, self.role = root, bundle_id, role
        self.root_fd, self.lock_fd, self.lock_name = root_fd, lock_fd, lock_name
        self.pid = os.getpid()
        self.files = {}
        self.published = False
        self.forked = False
        _leases.add(self)

    def valid(self):
        if self.forked or self.pid != os.getpid() or self.root_fd is None or self.lock_fd is None:
            return False
        try:
            _, current = _open_root(self.root)
            try:
                if _identity(os.fstat(current)) != _identity(os.fstat(self.root_fd)):
                    return False
            finally:
                os.close(current)
            opened = os.fstat(self.lock_fd)
            named = os.stat(self.lock_name, dir_fd=self.root_fd, follow_symlinks=False)
            return _regular(opened) and _regular(named) and _identity(opened) == _identity(named)
        except OSError:
            return False

    def _stat_for_preview(self, name):
        return os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)

    def _open_for_preview(self, name):
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.root_fd)

    def _matches_preview(self, name, info):
        current = self._stat_for_preview(name)
        return (_regular(current) and os.path.samestat(current, info)
                and current.st_size == info.st_size and current.st_ctime_ns == info.st_ctime_ns)

    def _open_for_cleanup(self, name):
        import fcntl
        fd = self._open_for_preview(name)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def remove_verified(self, observed, descriptors, record):
        # 원래 이름을 검사한 뒤 바로 unlink하면 그 사이 교체된 파일을 지울 수 있다.
        # 전용 디렉터리로 이동한 뒤에도 열린 파일과 같은지 비교한다.
        staging = f'.slp-cleanup-{self.bundle_id.owner}-{self.bundle_id.run_id}'
        try:
            os.mkdir(staging, mode=0o700, dir_fd=self.root_fd)
        except FileExistsError:
            return 'recovery_required'
        stage_fd = os.open(staging, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.root_fd)
        try:
            stage = os.fstat(stage_fd)
            if stage.st_uid != os.geteuid() or stage.st_mode & 0o077 or os.listdir(stage_fd):
                return 'recovery_required'
            # 한 파일씩 처리해 중단되더라도 종료 기록과 나머지 로그는 원래 위치에 둔다.
            all_names = {log_name(self.bundle_id, kind) for kind in supported_log_kinds(self.bundle_id)}
            remaining = set(observed)
            for name in [n for n in observed if n != record] + [record]:
                if not self.valid() or not self._matches_preview(name, observed[name]):
                    return 'file_changed'
                if any(_exists(self.root_fd, n) for n in all_names - remaining):
                    return 'file_set_changed'
                os.rename(name, name, src_dir_fd=self.root_fd, dst_dir_fd=stage_fd)
                current = os.stat(name, dir_fd=stage_fd, follow_symlinks=False)
                before = observed[name]
                remaining.remove(name)
                unexpected = (all_names | {record}) - remaining
                if (_regular(current) and os.path.samestat(current, os.fstat(descriptors[name]))
                        and current.st_size == before.st_size and current.st_mtime_ns == before.st_mtime_ns
                        and self.valid() and not any(_exists(self.root_fd, n) for n in unexpected)):
                    try:
                        os.unlink(name, dir_fd=stage_fd)
                    except OSError:
                        return 'recovery_required'
                    continue
                # 경합 중 생긴 새 파일은 덮어쓰지 않고 복구 사본을 보존한다.
                try:
                    os.link(name, name, src_dir_fd=stage_fd, dst_dir_fd=self.root_fd,
                            follow_symlinks=False)
                    os.unlink(name, dir_fd=stage_fd)
                except OSError:
                    return 'recovery_required'
                return 'file_changed'
            return 'deleted'
        finally:
            try:
                if (not os.listdir(stage_fd) and os.path.samestat(os.fstat(stage_fd),
                        self._stat_for_preview(staging))):
                    os.rmdir(staging, dir_fd=self.root_fd)
            finally:
                os.close(stage_fd)

    def open_log(self, kind):
        """writer lease 아래 새 파일만 생성한다. 반환 스트림의 종료는 호출자 책임이다."""
        if self.role != 'writer' or self.published or not self.valid():
            raise ValueError('INVALID_LOG_LEASE')
        name = log_name(self.bundle_id, kind)
        if name in self.files:
            raise ValueError('LOG_ALREADY_OPENED')
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.root_fd)
        reference = None
        try:
            if self.bundle_id.owner == 'worker':
                import fcntl
                # 전달·복제된 마지막 쓰기 설명자가 닫힐 때까지 같은 잠금이 유지된다.
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            # 쓰기 스트림 종료 후에도 원본 inode가 재사용되지 않도록 읽기 핸들을 보관한다.
            reference = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.root_fd)
            opened = os.fstat(reference)
            if not _regular(opened) or _identity(opened) != _identity(os.fstat(fd)):
                raise ValueError('LOG_FILE_CHANGED')
            stream = os.fdopen(fd, 'w', encoding='utf-8')
        except BaseException:
            if reference is not None:
                os.close(reference)
            os.close(fd)
            raise
        self.files[name] = (kind, reference, stream)
        return self.root / name, stream

    def release(self):
        # fork 자식에서 LOCK_UN을 호출하면 부모 잠금도 풀리므로 설명자만 닫는다.
        failure = None
        descriptors = [reference for _, reference, _ in self.files.values()]
        self.files.clear()
        descriptors.extend((self.lock_fd, self.root_fd))
        self.lock_fd = self.root_fd = None
        for fd in descriptors:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError as error:
                    failure = error
        if failure is not None:
            raise failure


class PosixBundleStore:
    def try_acquire_writer(self, root: Path, bundle_id: BundleId) -> LeaseResult:
        return self._acquire(root, bundle_id, 'writer')

    def try_acquire_inspector(self, root: Path, bundle_id: BundleId) -> LeaseResult:
        return self._acquire(root, bundle_id, 'inspector')

    def _acquire(self, root, bundle, role):
        if os.name != 'posix':
            return LeaseResult('unsupported')
        import fcntl
        root_fd = lock_fd = None
        try:
            path, root_fd = _open_root(root, create=role == 'writer')
            name = f'slp-log-v1-{bundle.owner}-{bundle.run_id}.lock'
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            if role == 'writer':
                flags |= os.O_CREAT | os.O_EXCL
            lock_fd = os.open(name, flags, 0o600, dir_fd=root_fd)
            if not _regular(os.fstat(lock_fd)):
                return LeaseResult('busy')
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lease = PosixLease(path, bundle, role, root_fd, lock_fd, name)
            if not lease.valid():
                return LeaseResult('busy')
            root_fd = lock_fd = None
            return LeaseResult('acquired', lease)
        except FileNotFoundError:
            return LeaseResult('missing')
        except OSError as error:
            return LeaseResult('busy' if error.errno in (errno.EAGAIN, errno.EEXIST, errno.ELOOP) else 'io_error')
        finally:
            for fd in (lock_fd, root_fd):
                if fd is not None:
                    with suppress(OSError):
                        os.close(fd)

    def publish_closed(self, lease, files: tuple[ClosedFile, ...], closed_at: datetime):
        if (not isinstance(lease, PosixLease) or lease.role != 'writer' or lease.published
                or not lease.valid() or not 1 <= len(files) <= 4):
            return 'invalid_state'
        temporary = f'.slp-closed-{uuid4().hex}.tmp'
        created = False
        linked = False
        try:
            if {item.name for item in files} != set(lease.files):
                return 'invalid_state'
            rows = []
            for item in files:
                kind, reference, stream = lease.files[item.name]
                if not stream.closed or kind != item.kind:
                    return 'invalid_state'
                if lease.bundle_id.owner == 'worker':
                    import fcntl
                    # 독립적으로 열린 원본 참조에서 상속된 writer가 남았는지 확인한다.
                    try:
                        fcntl.flock(reference, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        return 'invalid_state'
                fd = os.open(item.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=lease.root_fd)
                try:
                    info = os.fstat(fd)
                    if (not _regular(info) or _identity(info) != _identity(os.fstat(reference))
                            or info.st_size != item.size_bytes):
                        return 'invalid_state'
                finally:
                    os.close(fd)
                rows.append(dict(kind=kind, name=item.name, size_bytes=info.st_size,
                                 identity={'platform': 'posix', 'st_dev': info.st_dev,
                                           'st_ino': info.st_ino, 'st_size': info.st_size}))
            payload = json.dumps(dict(schema_version=1, owner=lease.bundle_id.owner,
                run_id=lease.bundle_id.run_id, closed_at=closed_at.isoformat().replace('+00:00', 'Z'),
                files=rows), separators=(',', ':')).encode('utf-8')
            parse_metadata_summary(payload, expected=lease.bundle_id)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=lease.root_fd)
            created = True
            try:
                output = os.fdopen(fd, 'wb')
            except BaseException:
                os.close(fd)
                raise
            with output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            if not lease.valid():
                return 'invalid_state'
            os.link(temporary, closed_name(lease.bundle_id), src_dir_fd=lease.root_fd,
                    dst_dir_fd=lease.root_fd, follow_symlinks=False)
            linked = True
            os.unlink(temporary, dir_fd=lease.root_fd)
            created = False
            lease.published = True
            return 'published'
        except FileExistsError:
            return 'invalid_state'
        except (ValueError, TypeError, KeyError):
            return 'invalid_state'
        except OSError:
            return 'io_error'
        finally:
            # link 이후의 정리 실패는 두 링크를 보존해 정상 종료로 오인하지 않게 한다.
            if created and not linked:
                with suppress(OSError):
                    os.unlink(temporary, dir_fd=lease.root_fd)
