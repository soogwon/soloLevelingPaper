"""Windows MCP 안전 로그의 실행 잠금과 정상 종료 기록 게시."""
import ctypes
from ctypes import wintypes as w
from contextlib import ExitStack
import json
import msvcrt
import os
from pathlib import Path
import stat
from uuid import uuid4

from .contracts import LeaseResult
from .policy import closed_name, log_name, parse_metadata_summary, supported_log_kinds


_kernel = ctypes.WinDLL('kernel32', use_last_error=True)


def _api(name, result, *args):
    fn = getattr(_kernel, name)
    fn.restype, fn.argtypes = result, args
    return fn


_create = _api('CreateFileW', w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID,
               w.DWORD, w.DWORD, w.HANDLE)
_close = _api('CloseHandle', w.BOOL, w.HANDLE)
_info = _api('GetFileInformationByHandleEx', w.BOOL, w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD)
_set_info = _api('SetFileInformationByHandle', w.BOOL, w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD)
_lock = _api('LockFileEx', w.BOOL, w.HANDLE, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.LPVOID)
_drive_type = _api('GetDriveTypeW', w.UINT, w.LPCWSTR)
_volume = _api('GetVolumeInformationW', w.BOOL, w.LPCWSTR, w.LPWSTR, w.DWORD,
               w.LPDWORD, w.LPDWORD, w.LPDWORD, w.LPWSTR, w.DWORD)


class _Overlapped(ctypes.Structure):
    _fields_ = [('internal', ctypes.c_size_t * 2), ('offset', w.DWORD * 2), ('event', w.HANDLE)]


def _open(path, access, *, create=False, directory=False, share=3):
    # 삭제 공유와 상속을 허용하지 않고 재분석 지점 자체를 검사한다.
    flags = 0x00200000 | (0x02000000 if directory else 0)
    handle = _create(str(path), access, share, None, 1 if create else 3, flags, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attrs = (w.DWORD * 2)()
        if not _info(handle, 9, ctypes.byref(attrs), ctypes.sizeof(attrs)):
            raise ctypes.WinError(ctypes.get_last_error())
        if attrs[0] & stat.FILE_ATTRIBUTE_REPARSE_POINT or bool(attrs[0] & 16) != directory:
            raise ValueError('UNSAFE_LOG_PATH')
        return handle
    except BaseException:
        _close(handle)
        raise


def _fd(path, access, flags, **kwargs):
    handle = _open(path, access, **kwargs)
    try:
        return msvcrt.open_osfhandle(handle, flags | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        _close(handle)
        raise


def _pin_root(root, handles, create):
    path = Path(os.path.abspath(root))
    fs = ctypes.create_unicode_buffer(32)
    if (len(path.drive) != 2 or _drive_type(path.anchor) != 3 or
            not _volume(path.anchor, None, 0, None, None, None, fs, len(fs)) or fs.value != 'NTFS'):
        raise ValueError('UNSUPPORTED_LOG_VOLUME')
    current = Path(path.anchor)
    for part in ('', *path.parts[1:]):
        if part and (part.endswith((' ', '.')) or ':' in part or Path(part).is_reserved()):
            raise ValueError('UNSAFE_LOG_PATH')
        current /= part
        try:
            handle = _open(current, 0x80, directory=True, share=1)
        except FileNotFoundError:
            if not create:
                raise
            current.mkdir(exist_ok=True)
            handle = _open(current, 0x80, directory=True, share=1)
        handles.callback(_close, handle)
    return path


def _rename_open_file(fd, destination):
    # 열린 임시 파일 자체를 이름 변경한다. 기존 종료 기록은 덮어쓰지 않는다.
    encoded = str(destination).encode('utf-16-le')
    class Rename(ctypes.Structure):
        _fields_ = [('flags', w.DWORD), ('root', w.HANDLE), ('length', w.DWORD),
                    ('name', ctypes.c_char * (len(encoded) + 2))]
    info = Rename()
    info.length = len(encoded)
    ctypes.memmove(ctypes.addressof(info) + Rename.name.offset, encoded, len(encoded))
    # FileNameLength가 있어도 일부 Windows 빌드는 가변 배열 뒤 바이트까지 이름으로
    # 관찰하므로 명시적 UTF-16 NUL을 포함하고 구조체 정렬 패딩은 제외한다.
    size = Rename.name.offset + len(encoded) + 2
    if not _set_info(msvcrt.get_osfhandle(fd), 3, ctypes.byref(info), size):
        raise ctypes.WinError(ctypes.get_last_error())


class WindowsLease:
    def __init__(self, path, bundle, role, handles):
        self.root, self.bundle_id, self.role = path, bundle, role
        self.handles, self.files = handles, {}
        self.published = False

    def release(self):
        handles, self.handles = self.handles, None
        if handles is not None:
            handles.close()

    def _stat_for_preview(self, name):
        return os.lstat(self.root / name)

    def _open_for_preview(self, name):
        return _fd(self.root / name, 0x80000000, os.O_RDONLY, share=1)

    def _open_for_cleanup(self, name):
        # DELETE 접근과 배타 공유로 열어 다른 사용자가 있는 파일은 건너뛴다.
        return _fd(self.root / name, 0x80010000, os.O_RDONLY, share=0)

    def remove_verified(self, observed, descriptors, record):
        # 검증한 핸들에만 삭제를 표시한다. 로그 표시 성공 후 종료 기록을 처리한다.
        disposition = ctypes.c_ubyte(1)
        absent = {log_name(self.bundle_id, kind) for kind in supported_log_kinds(self.bundle_id)} - observed.keys()
        for name in [n for n in observed if n != record] + [record]:
            for extra in absent:
                try:
                    self._stat_for_preview(extra)
                except FileNotFoundError:
                    continue
                return 'file_set_changed'
            handle = msvcrt.get_osfhandle(descriptors[name])
            if not _set_info(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
                raise ctypes.WinError(ctypes.get_last_error())
        return 'deleted'

    def _matches_preview(self, name, info):
        # 상위 경로와 파일의 핸들이 열려 있는 동안 쓰기·삭제 공유를 차단한다.
        return self.handles is not None

    def valid(self):
        return self.handles is not None

    def open_log(self, kind):
        name = log_name(self.bundle_id, kind)
        if self.handles is None or self.role != 'writer' or self.published or name in self.files:
            raise ValueError('INVALID_LOG_LEASE')
        path = self.root / name
        with ExitStack() as pending:
            stream = pending.enter_context(os.fdopen(_fd(path, 0x40000000, os.O_WRONLY, create=True),
                                                     'w', encoding='utf-8'))
            ref = _fd(path, 0x80000000, os.O_RDONLY)
            self.handles.callback(os.close, ref)
            self.files[name] = kind, ref, stream
            pending.pop_all()
        return path, stream


class WindowsBundleStore:
    def try_acquire_writer(self, root, bundle_id):
        return self._acquire(root, bundle_id, 'writer')

    def try_acquire_inspector(self, root, bundle_id):
        return self._acquire(root, bundle_id, 'inspector')

    def _acquire(self, root, bundle, role):
        try:
            with ExitStack() as handles:
                path = _pin_root(root, handles, role == 'writer')
                name = f'slp-log-v1-{bundle.owner}-{bundle.run_id}.lock'
                fd = _fd(path / name, 0xc0000000, os.O_RDWR, create=role == 'writer')
                handles.callback(os.close, fd)
                if os.fstat(fd).st_nlink != 1:
                    return LeaseResult('busy')
                if not _lock(msvcrt.get_osfhandle(fd), 3, 0, 1, 0, ctypes.byref(_Overlapped())):
                    raise ctypes.WinError(ctypes.get_last_error())
                return LeaseResult('acquired', WindowsLease(path, bundle, role, handles.pop_all()))
        except FileNotFoundError:
            return LeaseResult('missing')
        except ValueError:
            return LeaseResult('unsupported')
        except OSError as error:
            return LeaseResult('busy' if error.winerror in (32, 33, 80, 183) else 'io_error')

    def publish_closed(self, lease, files, closed_at):
        if (not isinstance(lease, WindowsLease) or lease.role != 'writer' or not lease.handles
                or lease.published or not files or len(files) != len(lease.files)):
            return 'invalid_state'
        try:
            rows = []
            if {item.name for item in files} != set(lease.files):
                return 'invalid_state'
            with ExitStack() as guards:
                for item in files:
                    kind, ref, stream = lease.files[item.name]
                    if not stream.closed or kind != item.kind:
                        return 'invalid_state'
                    fd = _fd(lease.root / item.name, 0x80000000, os.O_RDONLY, share=1)
                    guards.callback(os.close, fd)
                    info = os.fstat(fd)
                    if (not os.path.samestat(info, os.fstat(ref)) or info.st_nlink != 1
                            or info.st_size != item.size_bytes):
                        return 'invalid_state'
                    rows.append(dict(kind=kind, name=item.name, size_bytes=info.st_size,
                                     identity=dict(platform='windows', st_dev=info.st_dev,
                                                   st_ino=info.st_ino, st_size=info.st_size)))
                bundle = lease.bundle_id
                payload = json.dumps(dict(schema_version=1, owner=bundle.owner, run_id=bundle.run_id,
                    closed_at=closed_at.isoformat().replace('+00:00', 'Z'), files=rows)).encode()
                parse_metadata_summary(payload, expected=bundle)
                tmp = lease.root / f'.slp-closed-{uuid4().hex}.tmp'
                fd = _fd(tmp, 0x40010000, os.O_WRONLY, create=True, share=1)
                with os.fdopen(fd, 'wb') as out:
                    try:
                        out.write(payload)
                        out.flush()
                        os.fsync(fd)
                        _rename_open_file(fd, lease.root / closed_name(bundle))
                    except OSError:
                        # 실패한 임시 파일만 핸들로 지정해 닫을 때 정리한다.
                        discard = ctypes.c_ubyte(1)
                        _set_info(msvcrt.get_osfhandle(fd), 4, ctypes.byref(discard), 1)
                        raise
                lease.published = True
                return 'published'
        except (ValueError, KeyError, TypeError, FileExistsError):
            return 'invalid_state'
        except OSError:
            return 'io_error'
