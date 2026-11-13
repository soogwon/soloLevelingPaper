"""MCP가 접근할 수 있는 로컬 PDF 범위와 관리 복사본을 준비한다."""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath

from solo_leveling.application.evidence_qa.errors import InvalidArgumentError
from .errors import UnsupportedDocumentError
from solo_leveling.infrastructure.storage.pdf_source import open_pdf_source
from solo_leveling.infrastructure.storage.pdf_store import PdfStore


@dataclass(frozen=True)
class ManagedPdf:
    path: Path
    original_filename: str
    file_hash: str
    input_fingerprint: str


@dataclass(frozen=True)
class InspectedPdf:
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
        try:
            reader = open_pdf_source(source, self.import_root)
        except OSError:
            raise InvalidArgumentError('PDF 파일을 안전하게 열 수 없습니다.') from None
        with reader:
            if not 5 <= os.fstat(reader.fileno()).st_size <= self.max_bytes:
                raise UnsupportedDocumentError('PDF 파일 크기가 허용 범위를 벗어났습니다.')
            yield reader

    def _resolve_source(self, relative_path: str) -> tuple[Path, Path]:
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
        return requested, source

    @staticmethod
    def _fingerprint(requested: Path, file_hash: str) -> str:
        return hashlib.sha256(json.dumps({
            'kind': 'local_file', 'relative_path': requested.as_posix(),
            'file_hash': file_hash,
        }, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()

    def inspect_pdf(self, relative_path: str) -> InspectedPdf:
        """동의 카드 전에 파일을 복사하지 않고 등록 입력과 동일하게 검사한다."""
        requested, source = self._resolve_source(relative_path)
        digest = hashlib.sha256()
        header = b''
        with self._open_source(source) as reader:
            for block in iter(lambda: reader.read(65536), b''):
                header = (header + block[:5])[:5]
                if len(header) == 5 and header != b'%PDF-':
                    raise UnsupportedDocumentError('PDF 헤더를 확인할 수 없습니다.')
                digest.update(block)
        file_hash = digest.hexdigest()
        return InspectedPdf(source.name, file_hash,
                            self._fingerprint(requested, file_hash))

    def import_pdf(self, relative_path: str) -> ManagedPdf:
        requested, source = self._resolve_source(relative_path)

        with self._open_source(source) as reader:
            stored = PdfStore(self.managed_root, self.max_bytes).save_stream(reader)
        return ManagedPdf(stored.path, source.name, stored.file_hash,
                          self._fingerprint(requested, stored.file_hash))
