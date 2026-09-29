"""로컬 PDF 가져오기 경계와 관리 복사본을 검증한다."""

from pathlib import Path

import pytest

from solo_leveling.application.evidence_qa.errors import InvalidArgumentError
from solo_leveling.interfaces.mcp.errors import UnsupportedDocumentError
from solo_leveling.interfaces.mcp.local_files import LocalPdfStore


def store(tmp_path, max_bytes=100):
    imports = tmp_path / 'imports'
    imports.mkdir()
    return LocalPdfStore(imports, tmp_path / 'pdfs', max_bytes), imports


def test_import_copies_pdf_and_reuses_content_file(tmp_path):
    pdfs, imports = store(tmp_path)
    source = imports / 'paper.pdf'
    source.write_bytes(b'%PDF-1.7\ntest')
    first = pdfs.import_pdf('paper.pdf')
    second = pdfs.import_pdf('paper.pdf')
    assert first == second
    assert first.path.read_bytes() == source.read_bytes()
    assert first.path.parent == (tmp_path / 'pdfs').resolve()
    assert source.is_file()


@pytest.mark.parametrize('path', ['', '../paper.pdf', 'D:/paper.pdf'])
def test_invalid_paths_are_rejected(tmp_path, path):
    pdfs, _ = store(tmp_path)
    with pytest.raises(InvalidArgumentError):
        pdfs.import_pdf(path)


@pytest.mark.parametrize(('name', 'content'), [
    ('paper.txt', b'%PDF-1.7'), ('paper.pdf', b'not-a-pdf'),
])
def test_unsupported_documents_are_rejected(tmp_path, name, content):
    pdfs, imports = store(tmp_path)
    (imports / name).write_bytes(content)
    with pytest.raises(UnsupportedDocumentError):
        pdfs.import_pdf(name)


def test_size_limit_is_enforced(tmp_path):
    pdfs, imports = store(tmp_path, max_bytes=8)
    (imports / 'large.pdf').write_bytes(b'%PDF-' + b'x' * 20)
    with pytest.raises(UnsupportedDocumentError):
        pdfs.import_pdf('large.pdf')


def test_existing_managed_file_must_match_content(tmp_path):
    pdfs, imports = store(tmp_path)
    source = imports / 'paper.pdf'
    source.write_bytes(b'%PDF-1.7\ntest')
    imported = pdfs.import_pdf('paper.pdf')
    imported.path.write_bytes(b'x' * len(source.read_bytes()))
    with pytest.raises(RuntimeError):
        pdfs.import_pdf('paper.pdf')
