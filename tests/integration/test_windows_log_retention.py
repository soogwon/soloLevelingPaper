"""#99 Windows primitive experiments, not a production adapter or deletion API."""
import ctypes
from ctypes import wintypes
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows primitives')

if sys.platform == 'win32':
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetHandleInformation.restype = wintypes.BOOL

    class Overlapped(ctypes.Structure):
        _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                    ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD),
                    ('hEvent', wintypes.HANDLE)]

    kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel.LockFileEx.restype = wintypes.BOOL


def open_lock(path, *, create=False):
    # READ|WRITE sharing only: the locked name cannot be deleted/replaced.
    # OPEN_REPARSE_POINT: inspect links themselves, never follow them here.
    handle = kernel.CreateFileW(str(path), 0xC0000000, 3, None,
                                4 if create else 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def lock(handle):
    overlap = Overlapped()
    if not kernel.LockFileEx(handle, 3, 0, 1, 0, ctypes.byref(overlap)):
        raise ctypes.WinError(ctypes.get_last_error())


def probe(path):
    handle = open_lock(path)
    try:
        try:
            lock(handle)
        except OSError as error:
            if error.winerror == 33:
                return 'busy'
            raise
        return 'acquired'
    finally:
        kernel.CloseHandle(handle)


def child_probe(path):
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(path)],
                            capture_output=True, text=True, timeout=10, check=True)
    return result.stdout.strip()


def finish_child(child, *, timeout=10):
    """Always reap the test child, including a hung/failed handshake."""
    try:
        child.communicate('x', timeout=timeout)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate(timeout=10)


def test_nonblocking_lock_and_release_across_processes(tmp_path):
    path = tmp_path / 'bundle.lock'
    handle = open_lock(path, create=True)
    try:
        lock(handle)
        assert child_probe(path) == 'busy'
    finally:
        kernel.CloseHandle(handle)
    assert child_probe(path) == 'acquired'
    assert path.exists()  # Lock file is retained.


def test_missing_inspector_does_not_create_lock(tmp_path):
    path = tmp_path / 'missing.lock'
    with pytest.raises(OSError) as error:
        open_lock(path)
    assert error.value.winerror == 2
    assert not path.exists()


def test_lock_name_cannot_be_replaced_while_handle_is_open(tmp_path):
    path, replacement = tmp_path / 'bundle.lock', tmp_path / 'replacement'
    replacement.write_bytes(b'replacement')
    handle = open_lock(path, create=True)
    try:
        lock(handle)
        before = path.stat()
        for operation in (path.unlink, lambda: os.replace(replacement, path)):
            with pytest.raises(OSError) as error:
                operation()
            assert error.value.winerror in (5, 32)  # access denied / sharing violation
            after = path.stat()
            assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
            assert replacement.read_bytes() == b'replacement'
        assert child_probe(path) == 'busy'
    finally:
        kernel.CloseHandle(handle)


def test_handle_not_inherited_even_with_close_fds_false(tmp_path):
    path = tmp_path / 'bundle.lock'
    handle = open_lock(path, create=True)
    child = None
    reader = None
    try:
        lock(handle)
        flags = wintypes.DWORD()
        assert kernel.GetHandleInformation(handle, ctypes.byref(flags))
        assert not flags.value & 1  # HANDLE_FLAG_INHERIT
        child = subprocess.Popen([sys.executable, '-u', '-c',
            "import sys; print('ready', flush=True); sys.stdin.read(1)"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, close_fds=False)
        ready = threading.Event()
        output = []
        reader = threading.Thread(target=lambda: (output.append(child.stdout.readline()), ready.set()), daemon=True)
        reader.start()
        assert ready.wait(10) and output == ['ready\n']
        reader.join(10)
        assert not reader.is_alive()
        assert child_probe(path) == 'busy'
        kernel.CloseHandle(handle)
        handle = None
        assert child.poll() is None
        assert child_probe(path) == 'acquired'
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)
        if child is not None:
            # If the handshake reader is still blocked, stop the child before
            # communicate so two threads do not concurrently read stdout.
            if reader is not None and reader.is_alive():
                child.kill()
                child.wait(timeout=10)
                reader.join(10)
            finish_child(child)
            if reader is not None:
                assert not reader.is_alive()


def test_child_cleanup_kills_and_reaps_unresponsive_process():
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    finish_child(child, timeout=0.1)
    assert child.poll() is not None
    assert child.stdin.closed and child.stdout.closed


def test_identity_detects_replacement_and_hardlinks(tmp_path):
    path, replacement, alias = (tmp_path / name for name in ('log', 'new', 'alias'))
    path.write_bytes(b'old')
    before = path.stat()
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        assert (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino)
    replacement.write_bytes(b'new')
    os.replace(replacement, path)
    after = path.stat()
    assert (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
    os.link(path, alias)
    assert path.stat().st_nlink == 2
    assert alias.stat().st_ino == after.st_ino


def test_junction_is_identifiable_without_following(tmp_path):
    target, junction = tmp_path / 'target', tmp_path / 'junction'
    target.mkdir()
    subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(junction), str(target)],
                   capture_output=True, check=True, timeout=10)
    try:
        assert junction.lstat().st_file_attributes & 0x400  # REPARSE_POINT
        assert not target.lstat().st_file_attributes & 0x400
    finally:
        junction.rmdir()  # Remove only the test junction, not its target.
    assert target.is_dir()


def test_publish_is_complete_and_never_overwrites(tmp_path):
    payload = json.dumps({'schema_version': 1, 'synthetic': 'x' * 8000}).encode()
    for index in range(100):
        temporary, final = tmp_path / f'{index}.tmp', tmp_path / f'{index}.closed.json'
        with temporary.open('xb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        ready, done, observed = threading.Event(), threading.Event(), threading.Event()
        failures = []
        def read():
            ready.set()
            while not done.is_set():
                try:
                    if final.read_bytes() != payload:
                        failures.append('partial')
                    observed.set()
                except FileNotFoundError:
                    pass
                except OSError:
                    failures.append('io_error')
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            assert ready.wait(10)
            os.link(temporary, final)
            assert observed.wait(10)  # Reader actually saw the published file.
            assert final.read_bytes() == payload
            competitor = tmp_path / f'{index}.competitor'
            competitor.write_bytes(b'different payload')
            before = final.stat()
            with pytest.raises(FileExistsError):
                os.link(competitor, final)
            assert final.read_bytes() == payload
            assert (final.stat().st_dev, final.stat().st_ino) == (before.st_dev, before.st_ino)
        finally:
            done.set()
            reader.join(10)
        assert not reader.is_alive() and failures == []
        temporary.unlink()
        assert final.stat().st_nlink == 1


def test_two_publishers_have_one_winner(tmp_path):
    final = tmp_path / 'bundle.closed.json'
    sources = [tmp_path / 'first.tmp', tmp_path / 'second.tmp']
    for index, path in enumerate(sources):
        with path.open('xb') as stream:
            stream.write(f'publisher-{index}'.encode())
            stream.flush()
            os.fsync(stream.fileno())
    barrier = threading.Barrier(2, timeout=10)
    results = []
    def publish(path):
        try:
            barrier.wait()
            os.link(path, final)
            results.append(('published', path))
        except FileExistsError:
            results.append(('exists', path))
        except Exception:
            results.append(('unexpected_error', path))
    threads = [threading.Thread(target=publish, args=(path,), daemon=True) for path in sources]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert not any(thread.is_alive() for thread in threads)
    assert sorted(result[0] for result in results) == ['exists', 'published']
    winner = next(path for state, path in results if state == 'published')
    assert final.read_bytes() == winner.read_bytes()
    assert final.stat().st_ino == winner.stat().st_ino


def _publish_in_child(source, final, barrier):
    barrier.wait(timeout=15)
    try:
        os.link(source, final)
    except FileExistsError:
        raise SystemExit(2)


def test_separate_processes_cannot_overwrite_closed_metadata(tmp_path):
    ctx = multiprocessing.get_context('spawn')
    barrier = ctx.Barrier(2)
    final = tmp_path / 'bundle.closed.json'
    sources = [tmp_path / 'first.tmp', tmp_path / 'second.tmp']
    children = []
    try:
        for source in sources:
            source.write_bytes(source.name.encode())
            child = ctx.Process(target=_publish_in_child, args=(source, final, barrier))
            child.start()
            children.append(child)
        for child in children:
            child.join(timeout=25)
        codes = [child.exitcode for child in children]
        assert codes.count(0) == codes.count(2) == 1, codes
        winner = sources[codes.index(0)]
        assert final.samefile(winner)
        assert final.read_bytes() == winner.name.encode()
    finally:
        for child in children:
            if child.is_alive():
                child.kill()
            child.join()
            child.close()


if __name__ == '__main__':
    print(probe(Path(sys.argv[1])))
