"""검사 잠금 아래 MCP·worker 종료 기록과 파일을 대조하는 읽기 전용 미리보기."""
from contextlib import ExitStack
import errno
import json
import os
import stat

from .policy import MAX_METADATA_BYTES, closed_name, log_name, parse_metadata_summary, retention_status, supported_log_kinds


def _open_checked(lease, name, stack):
    fd = lease._open_for_preview(name)
    stack.callback(os.close, fd)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('unsafe_file')
    return fd, info


def _inspect(lease, scanned, now, policy):
    record = closed_name(lease.bundle_id)
    with ExitStack() as stack:
        try:
            fd, metadata = _open_checked(lease, record, stack)
        except FileNotFoundError:
            return 'missing_closed_record'
        if metadata.st_size > MAX_METADATA_BYTES:
            return 'invalid_metadata'
        payload = os.read(fd, MAX_METADATA_BYTES + 1)
        summary = parse_metadata_summary(payload, expected=lease.bundle_id)
        if {item.name for item in summary.files} != set(scanned):
            return 'file_set_changed'
        observed = {record: metadata}
        # 공통 파서는 이름과 필드를 검증하고 OS 식별자의 해석은 여기에 맡긴다.
        for row in json.loads(payload)['files']:
            name = row['name']
            initial = scanned[name]
            if initial is None:
                return 'unsafe_file'
            _, info = _open_checked(lease, name, stack)
            if (not os.path.samestat(initial, info)
                    or initial.st_size != info.st_size or initial.st_mtime_ns != info.st_mtime_ns):
                return 'file_changed'
            observed[name] = info
            expected = dict(platform='windows' if os.name == 'nt' else 'posix',
                            st_dev=info.st_dev, st_ino=info.st_ino, st_size=info.st_size)
            identity = row['identity']
            if (not isinstance(identity, dict) or identity.keys() != expected.keys()
                    or any(type(identity[k]) is not type(v) for k, v in expected.items())):
                return 'invalid_metadata'
            if identity != expected or row['size_bytes'] != info.st_size:
                return 'file_changed'
            # 종료 기록 작성 뒤 바뀐 파일은 같은 크기·재사용된 inode여도 제외한다.
            if max(info.st_ctime_ns, info.st_mtime_ns) > metadata.st_mtime_ns:
                return 'file_changed'
        # 최초 목록을 만든 뒤 추가된 원본·스택 파일까지 잠금 아래 다시 확인한다.
        for kind in supported_log_kinds(lease.bundle_id):
            name = log_name(lease.bundle_id, kind)
            if name not in scanned:
                try:
                    lease._stat_for_preview(name)
                except FileNotFoundError:
                    continue
                return 'file_set_changed'
        if not lease.valid() or not all(lease._matches_preview(n, s) for n, s in observed.items()):
            return 'file_changed'
        return retention_status(summary, now=now, policy=policy)


def inspect_bundle(root, bundle, scanned, now, policy):
    if os.name == 'posix':
        from .posix import PosixBundleStore
        store = PosixBundleStore()
    elif os.name == 'nt':
        from .windows import WindowsBundleStore
        store = WindowsBundleStore()
    else:
        return 'unsupported'
    result = store.try_acquire_inspector(root, bundle)
    if result.status != 'acquired':
        return {'busy': 'lock_busy', 'missing': 'missing_lock'}.get(result.status, result.status)
    try:
        try:
            return _inspect(result.lease, scanned, now, policy)
        finally:
            result.lease.release()
    except FileNotFoundError:
        return 'file_changed'
    except ValueError:
        return 'invalid_metadata'
    except OSError as exc:
        return 'unsafe_file' if exc.errno == errno.ELOOP else 'io_error'
