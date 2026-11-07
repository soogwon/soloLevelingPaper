"""Windows에서 검사한 디렉터리 경계를 유지한 채 PDF 읽기 핸들을 연다."""

from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import msvcrt
import os
from pathlib import Path
import stat


_kernel = ctypes.WinDLL('kernel32', use_last_error=True)
_create = _kernel.CreateFileW
_create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_create.restype = wintypes.HANDLE
_close = _kernel.CloseHandle
_close.argtypes = [wintypes.HANDLE]
_info = _kernel.GetFileInformationByHandleEx
_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]


def _open_component(path, *, directory):
    # 재분석 지점 자체를 열고, 열린 동안 쓰기·삭제 공유를 허용하지 않는다.
    flags = 0x00200000 | (0x02000000 if directory else 0)
    handle = _create(str(path), 0x80000000, 1, None, 3, flags, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # FileAttributeTagInfo는 파일 속성과 재분석 태그 두 DWORD로 구성된다.
        attributes = (wintypes.DWORD * 2)()
        if not _info(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes)):
            raise ctypes.WinError(ctypes.get_last_error())
        flags = attributes[0]
        if flags & stat.FILE_ATTRIBUTE_REPARSE_POINT or bool(flags & stat.FILE_ATTRIBUTE_DIRECTORY) != directory:
            raise OSError('UNSAFE_PDF_PATH')
        return handle
    except BaseException:
        _close(handle)
        raise


def open_pdf_descriptor(source: Path, import_root: Path) -> int:
    # 대소문자를 구분하는 Windows 디렉터리에서도 다른 루트를 허용하지 않는다.
    if source.parts[:len(import_root.parts)] != import_root.parts:
        raise OSError('OUTSIDE_IMPORT_ROOT')
    with ExitStack() as parents:
        for parent in reversed(source.parents):
            parents.callback(_close, _open_component(parent, directory=True))
        handle = _open_component(source, directory=False)
        try:
            # 반환된 설명자가 핸들을 소유한다. 이후 복사는 경로를 다시 열지 않는다.
            return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
        except BaseException:
            _close(handle)
            raise
