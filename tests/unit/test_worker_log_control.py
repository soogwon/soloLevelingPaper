"""진단 핸들 수신과 파이프 정리 실패를 실제 운영체제 자원으로 검증한다."""
import os
from unittest.mock import Mock

import pytest

from solo_leveling.infrastructure.embeddings import worker


@pytest.mark.parametrize('raw', [False, True])
@pytest.mark.parametrize('target_fails', [False, True])
def test_close_failure_discards_received_fds_and_runs_target_once(
        tmp_path, monkeypatch, capsys, raw, target_fails):
    kinds = ('raw', 'stack') if raw else ('safe',)
    received = {kind: os.open(os.devnull, os.O_WRONLY) for kind in kinds}
    control = Mock()
    control.close.side_effect = OSError('PRIVATE_CONTROL')
    monkeypatch.setattr(worker, '_receive_log_fds', lambda _: received)
    discard = Mock()
    monkeypatch.setattr(worker, '_discard_output', discard)
    connection = Mock()

    def run(conn):
        for fd in received.values():
            with pytest.raises(OSError):
                os.fstat(fd)
        if target_fails:
            raise ValueError('PRIVATE_TARGET')
        conn.send({'ok': True})

    target = Mock(side_effect=run)
    worker.diagnostic_worker(connection, target, tmp_path, 'a' * 32, 10, raw, log_control=control)
    target.assert_called_once_with(connection)
    control.close.assert_called_once_with()
    discard.assert_called_once_with()
    connection.send.assert_called_once_with({'ok': not target_fails})
    assert capsys.readouterr() == ('', '')
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name != 'nt', reason='Windows 네이티브 핸들 소유권 검증')
@pytest.mark.parametrize('fail_at', [0, 1])
def test_windows_conversion_failure_closes_each_received_handle(monkeypatch, fail_at):
    import _winapi
    import ctypes
    from ctypes import wintypes
    import msvcrt
    from multiprocessing import reduction

    info = ctypes.WinDLL('kernel32', use_last_error=True).GetHandleInformation
    info.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    info.restype = wintypes.BOOL
    handles = []
    converted = []
    convert = msvcrt.open_osfhandle
    process = _winapi.GetCurrentProcess()

    def receive(_):
        with open(os.devnull, 'wb') as source:
            handle = _winapi.DuplicateHandle(process, msvcrt.get_osfhandle(source.fileno()),
                                            process, 0, False, _winapi.DUPLICATE_SAME_ACCESS)
        handles.append(handle)
        return handle

    def convert_until_failure(handle, flags):
        if len(converted) == fail_at:
            raise OSError('PRIVATE_CONVERSION')
        fd = convert(handle, flags)
        converted.append(fd)
        return fd

    control = Mock()
    control.recv.return_value = (True, ('raw', 'stack'))
    monkeypatch.setattr(reduction, 'recv_handle', receive)
    monkeypatch.setattr(msvcrt, 'open_osfhandle', convert_until_failure)
    close_native = Mock(wraps=_winapi.CloseHandle)
    monkeypatch.setattr(_winapi, 'CloseHandle', close_native)
    assert worker._receive_log_fds(control) == {}
    close_native.assert_called_once_with(handles[fail_at])
    assert len(handles) == fail_at + 1
    for handle in handles:
        assert not info(handle, ctypes.byref(wintypes.DWORD()))
        assert ctypes.get_last_error() == 6
    for fd in converted:
        with pytest.raises(OSError):
            os.fstat(fd)
