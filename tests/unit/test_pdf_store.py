"""공용 PDF 저장 계약과 로컬 입력의 호환성을 검증한다."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from solo_leveling.infrastructure.storage.pdf_store import PdfStore, UnsupportedDocumentError
from solo_leveling.interfaces.mcp.local_files import LocalPdfStore


PDF = b'%PDF-1.7\nsynthetic content'


def test_bytes_stream_and_local_input_share_file(tmp_path):
    root = tmp_path / 'pdfs'
    store = PdfStore(root, len(PDF))
    first = store.save_bytes(PDF)
    stream = BytesIO(PDF)
    assert store.save_stream(stream) == first
    assert not stream.closed
    assert stream.tell() == len(PDF)
    assert first.size_bytes == len(PDF)
    assert first.file_hash == hashlib.sha256(PDF).hexdigest()
    assert first.path == root / f'{first.file_hash}.pdf'
    imports = tmp_path / 'imports'
    imports.mkdir()
    (imports / 'paper.pdf').write_bytes(PDF)
    local = LocalPdfStore(imports, root, len(PDF)).import_pdf('paper.pdf')
    assert local.path == first.path
    assert local.original_filename == 'paper.pdf'
    expected = hashlib.sha256(json.dumps({
        'kind': 'local_file', 'relative_path': 'paper.pdf', 'file_hash': first.file_hash,
    }, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert local.input_fingerprint == expected
    assert list(root.iterdir()) == [first.path]


@pytest.mark.parametrize('content', [b'', b'%PDF', b'not a pdf', PDF + b'x'])
@pytest.mark.parametrize('method', ['save_bytes', 'save_stream'])
def test_invalid_content_leaves_no_files(tmp_path, content, method):
    root = tmp_path / 'pdfs'
    value = content if method == 'save_bytes' else BytesIO(content)
    with pytest.raises(UnsupportedDocumentError):
        getattr(PdfStore(root, len(PDF)), method)(value)
    assert not root.exists() or list(root.iterdir()) == []


def test_short_reads_and_current_position(tmp_path):
    class ShortReader(BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 2))

    stream = ShortReader(b'prefix' + PDF)
    stream.seek(6)
    result = PdfStore(tmp_path, 100).save_stream(stream)
    assert result.path.read_bytes() == PDF
    assert not stream.closed


def test_interrupted_stream_preserves_existing_file(tmp_path):
    store = PdfStore(tmp_path, 100)
    saved = store.save_bytes(PDF)

    class BrokenReader:
        calls = 0

        def read(self, size):
            self.calls += 1
            if self.calls == 1:
                return b'%PDF-'
            raise OSError('읽기 중단')

    with pytest.raises(OSError):
        store.save_stream(BrokenReader())
    assert list(tmp_path.iterdir()) == [saved.path]
    assert saved.path.read_bytes() == PDF


def test_corrupt_existing_file_is_not_overwritten(tmp_path):
    store = PdfStore(tmp_path, 100)
    saved = store.save_bytes(PDF)
    saved.path.write_bytes(b'x' * len(PDF))
    with pytest.raises(RuntimeError):
        store.save_bytes(PDF)
    assert saved.path.read_bytes() == b'x' * len(PDF)
    assert list(tmp_path.iterdir()) == [saved.path]


def test_concurrent_same_content_is_reused(tmp_path):
    store = PdfStore(tmp_path, 100)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(store.save_bytes, [PDF] * 8))
    assert all(result == results[0] for result in results)
    assert list(tmp_path.iterdir()) == [results[0].path]
    assert results[0].path.read_bytes() == PDF


def test_publish_error_cleans_temporary_file(tmp_path):
    with patch('solo_leveling.infrastructure.storage.pdf_store.os.link',
               side_effect=OSError('저장 실패')):
        with pytest.raises(OSError):
            PdfStore(tmp_path, 100).save_bytes(PDF)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('block', [None, '', '%PDF-'])
def test_non_binary_stream_is_rejected(tmp_path, block):
    class InvalidReader:
        def read(self, size):
            return block

    with pytest.raises(TypeError):
        PdfStore(tmp_path, 100).save_stream(InvalidReader())
    assert list(tmp_path.iterdir()) == []


def test_existing_directory_is_not_replaced(tmp_path):
    destination = tmp_path / f'{hashlib.sha256(PDF).hexdigest()}.pdf'
    destination.mkdir()
    with pytest.raises((RuntimeError, OSError)):
        PdfStore(tmp_path, 100).save_bytes(PDF)
    assert destination.is_dir()
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize('limit', [True, 0, -1, 1.5, '100'])
def test_invalid_limit(tmp_path, limit):
    with pytest.raises(ValueError):
        PdfStore(tmp_path, limit)


def test_common_module_does_not_load_mcp(tmp_path):
    env = dict(os.environ)
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2] / 'src')
    result = subprocess.run([sys.executable, '-B', '-c',
        'import sys; from solo_leveling.infrastructure.storage.pdf_store import PdfStore; '
        'assert not any(k == "mcp" or k.startswith("mcp.") or '
        'k.startswith("solo_leveling.interfaces.mcp") for k in sys.modules)'],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_mcp_error_type_remains_compatible():
    from solo_leveling.interfaces.mcp.errors import UnsupportedDocumentError as MCPError
    from solo_leveling.interfaces.mcp.errors import to_tool_error

    assert MCPError is UnsupportedDocumentError
    assert str(to_tool_error(UnsupportedDocumentError('비공개 상세'))) == (
        'UNSUPPORTED_DOCUMENT: 지원하는 PDF 파일인지 확인해주세요.')
