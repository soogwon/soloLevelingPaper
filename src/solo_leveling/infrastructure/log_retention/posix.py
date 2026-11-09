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
from .policy import closed_name, log_name, parse_metadata_summary


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
