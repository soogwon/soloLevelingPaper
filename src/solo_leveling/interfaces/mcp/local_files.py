"""MCP가 접근할 수 있는 로컬 PDF 범위와 관리 복사본을 준비한다."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

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

    def import_pdf(self, relative_path: str) -> ManagedPdf:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise InvalidArgumentError('상대 PDF 경로가 필요합니다.')
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

        with source.open('rb') as reader:
            stored = PdfStore(self.managed_root, self.max_bytes).save_stream(reader)
        normalized = requested.as_posix()
        fingerprint = hashlib.sha256(json.dumps({
            'kind': 'local_file', 'relative_path': normalized, 'file_hash': stored.file_hash,
        }, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        return ManagedPdf(stored.path, source.name, stored.file_hash, fingerprint)
