"""MCP가 접근할 수 있는 로컬 PDF 범위와 관리 복사본을 준비한다."""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
import stat
from pathlib import Path, PureWindowsPath

from solo_leveling.application.evidence_qa.errors import InvalidArgumentError
from .errors import UnsupportedDocumentError
from solo_leveling.infrastructure.storage.pdf_store import PdfStore


@dataclass(frozen=True)
class ManagedPdf:
    path: Path
    original_filename: str
    file_hash: str
    input_fingerprint: str


class LocalPdfStore:
    def __init__(self, import_root: Path, managed_root: Path, max_bytes: int):
        self.import_root = import_root.resolve()
        self.managed_root = managed_root.resolve()
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError('MAX_UPLOAD_BYTES는 양의 정수여야 합니다.')
        self.max_bytes = max_bytes

    @contextmanager
    def _open_source(self, source: Path):
        """열린 파일을 검사한 뒤에만 읽는다. POSIX에서는 디렉터리별 링크 추적도 막는다."""
        descriptor = None
        directory = None
        try:
            if os.name == 'posix':
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                directory = os.open(self.import_root, flags)
                parts = source.relative_to(self.import_root).parts
                for part in parts[:-1]:
                    child = os.open(part, flags, dir_fd=directory)
                    os.close(directory)
                    directory = child
                descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=directory)
            elif os.name == 'nt':
                from .windows_pdf import open_pdf_descriptor
                descriptor = open_pdf_descriptor(source, self.import_root)
            else:
                descriptor = os.open(source, os.O_RDONLY | getattr(os, 'O_BINARY', 0))
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise InvalidArgumentError('단일 링크의 일반 PDF 파일만 가져올 수 있습니다.')
            if not 5 <= opened.st_size <= self.max_bytes:
                raise UnsupportedDocumentError('PDF 파일 크기가 허용 범위를 벗어났습니다.')
            reader = os.fdopen(descriptor, 'rb')
            descriptor = None  # 소유권을 스트림에 넘긴다.
        except OSError:
            raise InvalidArgumentError('PDF 파일을 안전하게 열 수 없습니다.') from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if directory is not None:
                os.close(directory)
        with reader:
            yield reader

    def import_pdf(self, relative_path: str) -> ManagedPdf:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise InvalidArgumentError('상대 PDF 경로가 필요합니다.')
        # OS별 경로 해석 차이와 Windows 대체 데이터 스트림 접근을 차단한다.
        windows_path = PureWindowsPath(relative_path)
        if ('\x00' in relative_path or ':' in relative_path or windows_path.drive
                or windows_path.root or '..' in windows_path.parts):
            raise InvalidArgumentError('import 루트 아래 상대 경로만 사용할 수 있습니다.')
        requested = Path(relative_path)
        if requested.is_absolute() or '..' in requested.parts:
            raise InvalidArgumentError('import 루트 아래 상대 경로만 사용할 수 있습니다.')
        source = (self.import_root / requested).resolve()
        if source == self.import_root or self.import_root not in source.parents:
            raise InvalidArgumentError('파일 경로가 import 루트를 벗어났습니다.')
        if not source.is_file():
            raise InvalidArgumentError('PDF 파일을 찾을 수 없습니다.')
        if source.suffix.lower() != '.pdf':
            raise UnsupportedDocumentError('PDF 확장자만 지원합니다.')
        size = source.stat().st_size
        if size < 5 or size > self.max_bytes:
            raise UnsupportedDocumentError('PDF 파일 크기가 허용 범위를 벗어났습니다.')

        with self._open_source(source) as reader:
            stored = PdfStore(self.managed_root, self.max_bytes).save_stream(reader)
        normalized = requested.as_posix()
        fingerprint = hashlib.sha256(json.dumps({
            'kind': 'local_file', 'relative_path': normalized, 'file_hash': stored.file_hash,
        }, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        return ManagedPdf(stored.path, source.name, stored.file_hash, fingerprint)
