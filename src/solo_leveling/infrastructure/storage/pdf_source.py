"""허용된 루트 아래 PDF를 경로 교체에 안전한 읽기 핸들로 연다."""
import os
from pathlib import Path
import stat
from typing import BinaryIO


def open_pdf_source(source: Path, root: Path) -> BinaryIO:
    """호출자는 반환된 스트림을 닫는다. root는 정규화된 신뢰 경로이다."""
    if not source.is_relative_to(root):
        raise OSError('OUTSIDE_PDF_ROOT')
    parts = source.relative_to(root).parts
    if not parts or '..' in parts:
        raise OSError('UNSAFE_PDF_PATH')
    if os.name == 'posix':
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory = os.open(root, flags)
        try:
            for part in parts[:-1]:
                child = os.open(part, flags, dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
        finally:
            os.close(directory)
    elif os.name == 'nt':
        from .windows_pdf import open_pdf_descriptor
        descriptor = open_pdf_descriptor(source, root)
    else:
        raise OSError('UNSUPPORTED_PDF_PLATFORM')
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise OSError('UNSAFE_PDF_FILE')
        return os.fdopen(descriptor, 'rb')
    except BaseException:
        os.close(descriptor)
        raise
