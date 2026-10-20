"""PDF 바이트를 해시 기반 관리 저장소에 보관한다."""

from dataclasses import dataclass
import hashlib
from io import BytesIO
import os
from pathlib import Path
from typing import BinaryIO
import uuid


class UnsupportedDocumentError(ValueError):
    """입력 데이터가 PDF 헤더·크기 조건을 만족하지 않는다."""


@dataclass(frozen=True)
class StoredPdf:
    path: Path
    file_hash: str
    size_bytes: int


class PdfStore:
    """저장 위치는 호출자가 지정하며 다운로드·DB 기록은 수행하지 않는다."""

    def __init__(self, managed_root: Path, max_bytes: int):
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError('MAX_UPLOAD_BYTES는 양의 정수여야 합니다.')
        self.managed_root = managed_root.resolve()
        self.max_bytes = max_bytes

    def save_bytes(self, content: bytes) -> StoredPdf:
        """이미 내려받은 바이트를 저장한다. 다운로드 용량 제한은 호출자 책임이다."""
        if not isinstance(content, bytes):
            raise TypeError('PDF 입력은 bytes여야 합니다.')
        if not 5 <= len(content) <= self.max_bytes:
            raise UnsupportedDocumentError('PDF 파일 크기가 허용 범위를 벗어났습니다.')
        with BytesIO(content) as stream:
            return self.save_stream(stream)

    def save_stream(self, stream: BinaryIO) -> StoredPdf:
        """현재 위치부터 읽으며 호출자 스트림을 닫거나 되감지 않는다."""
        self.managed_root.mkdir(parents=True, exist_ok=True)
        temporary = self.managed_root / f'.import-{uuid.uuid4()}.tmp'
        digest = hashlib.sha256()
        copied = 0
        header = b''
        try:
            with temporary.open('xb') as writer:
                while True:
                    block = stream.read(65536)
                    if not isinstance(block, bytes):
                        raise TypeError('PDF 스트림은 bytes를 반환해야 합니다.')
                    if not block:
                        break
                    copied += len(block)
                    if copied > self.max_bytes:
                        raise UnsupportedDocumentError('PDF 파일 크기가 허용 범위를 벗어났습니다.')
                    header = (header + block[:5])[:5]
                    if len(header) == 5 and header != b'%PDF-':
                        raise UnsupportedDocumentError('PDF 헤더를 확인할 수 없습니다.')
                    writer.write(block)
                    digest.update(block)
            if copied < 5:
                raise UnsupportedDocumentError('PDF 헤더를 확인할 수 없습니다.')
            file_hash = digest.hexdigest()
            destination = self.managed_root / f'{file_hash}.pdf'
            try:
                # 같은 내용의 동시 저장 시에도 기존 파일을 덮어쓰지 않는다.
                os.link(temporary, destination)
            except FileExistsError:
                if destination.is_symlink() or not destination.is_file():
                    raise RuntimeError('관리 PDF 저장소의 기존 경로가 일반 파일이 아닙니다.')
                with destination.open('rb') as existing:
                    existing_hash = hashlib.file_digest(existing, 'sha256').hexdigest()
                if destination.stat().st_size != copied or existing_hash != file_hash:
                    raise RuntimeError('관리 PDF 저장소의 기존 파일이 일치하지 않습니다.')
            return StoredPdf(destination, file_hash, copied)
        finally:
            temporary.unlink(missing_ok=True)
