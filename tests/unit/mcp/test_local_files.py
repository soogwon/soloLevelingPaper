"""로컬 PDF 가져오기 경계와 관리 복사본을 검증한다."""

import errno
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


@pytest.mark.parametrize('path', [
    '', ' ', '../paper.pdf', '/paper.pdf', 'D:/paper.pdf',
    r'D:\paper.pdf', 'D:paper.pdf', r'\paper.pdf',
    r'\\server\share\paper.pdf', '//server/share/paper.pdf',
    r'\\?\D:\paper.pdf', r'..\paper.pdf', r'folder\..\paper.pdf',
    'folder/../paper.pdf', 'paper.pdf:secret.pdf', 'paper\x00.pdf',
    None, 123,
])
def test_invalid_paths_are_rejected(tmp_path, path):
    pdfs, _ = store(tmp_path)
    with pytest.raises(InvalidArgumentError):
        pdfs.import_pdf(path)


@pytest.mark.parametrize('path', ['D:paper.pdf', r'..\paper.pdf', 'paper.pdf:secret.pdf'])
def test_unsafe_paths_are_rejected_before_filesystem_access(tmp_path, monkeypatch, path):
    pdfs, _ = store(tmp_path)

    def unexpected_resolve(*args, **kwargs):
        pytest.fail('잘못된 입력은 파일 경로 조회 전에 거부해야 합니다.')

    monkeypatch.setattr(Path, 'resolve', unexpected_resolve)
    with pytest.raises(InvalidArgumentError):
        pdfs.import_pdf(path)


def test_nested_korean_filename_is_supported(tmp_path):
    pdfs, imports = store(tmp_path)
    (imports / '논문').mkdir()
    (imports / '논문' / '어텐션.PDF').write_bytes(b'%PDF-1.7\ntest')
    result = pdfs.import_pdf('논문/어텐션.PDF')
    assert result.original_filename == '어텐션.PDF'
    assert result.path.read_bytes() == b'%PDF-1.7\ntest'


def test_resolved_path_outside_root_is_rejected_before_open(tmp_path, monkeypatch):
    pdfs, imports = store(tmp_path)
    outside = tmp_path / 'private.pdf'
    original_resolve = Path.resolve

    def resolve_outside(path, *args, **kwargs):
        if path == imports / 'paper.pdf':
            return outside
        return original_resolve(path, *args, **kwargs)

    def unexpected_open(*args, **kwargs):
        pytest.fail('허용 폴더 밖 파일을 열면 안 됩니다.')

    monkeypatch.setattr(Path, 'resolve', resolve_outside)
    monkeypatch.setattr(Path, 'open', unexpected_open)
    with pytest.raises(InvalidArgumentError, match='import 루트'):
        pdfs.import_pdf('paper.pdf')


@pytest.mark.parametrize('name', ['missing.pdf', 'directory.pdf'])
def test_missing_file_and_directory_are_rejected(tmp_path, name):
    pdfs, imports = store(tmp_path)
    (imports / 'directory.pdf').mkdir()
    with pytest.raises(InvalidArgumentError):
        pdfs.import_pdf(name)


def symlink_or_skip(link, target, *, directory=False):
    """권한 부족·기능 미지원만 건너뛰고 나머지 파일 시스템 오류는 드러낸다."""
    try:
        link.symlink_to(target, target_is_directory=directory)
    except NotImplementedError:
        pytest.skip('이 환경에서는 심볼릭 링크 생성을 지원하지 않습니다.')
    except OSError as error:
        if (error.errno in {errno.EACCES, errno.EPERM, errno.ENOSYS, errno.ENOTSUP}
                or getattr(error, 'winerror', None) == 1314):
            pytest.skip('심볼릭 링크 생성 권한이 없거나 파일 시스템에서 지원하지 않습니다.')
        raise


@pytest.mark.parametrize('error', [
    OSError(errno.EACCES, '권한 부족'), OSError(errno.EPERM, '작업 불허'),
    OSError(errno.ENOSYS, '기능 미지원'), OSError(errno.ENOTSUP, '지원하지 않음'),
    NotImplementedError(),
])
def test_symlink_expected_environment_errors_are_skipped(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(Path, 'symlink_to', fail)
    with pytest.raises(pytest.skip.Exception):
        symlink_or_skip(Path('link'), Path('target'))


@pytest.mark.parametrize('code', [errno.EIO, errno.ENOENT, errno.EEXIST, errno.ENOSPC])
def test_symlink_unexpected_errors_are_not_skipped(monkeypatch, code):
    error = OSError(code, '예상하지 못한 파일 시스템 오류')

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(Path, 'symlink_to', fail)
    with pytest.raises(OSError) as caught:
        symlink_or_skip(Path('link'), Path('target'))
    assert caught.value is error


@pytest.mark.parametrize('directory_link', [False, True])
def test_symlink_outside_import_root_is_rejected(tmp_path, directory_link):
    pdfs, imports = store(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'paper.pdf').write_bytes(b'%PDF-1.7\nprivate')
    link = imports / ('linked' if directory_link else 'paper.pdf')
    symlink_or_skip(link, outside if directory_link else outside / 'paper.pdf',
                    directory=directory_link)
    with pytest.raises(InvalidArgumentError):
        pdfs.import_pdf('linked/paper.pdf' if directory_link else 'paper.pdf')
    assert not (tmp_path / 'pdfs').exists()


@pytest.mark.parametrize('content', [b'', b'%PDF', b'%PDF-' + b'x' * 96])
def test_invalid_size_does_not_create_managed_copy(tmp_path, content):
    pdfs, imports = store(tmp_path)
    (imports / 'paper.pdf').write_bytes(content)
    with pytest.raises(UnsupportedDocumentError):
        pdfs.import_pdf('paper.pdf')
    assert not (tmp_path / 'pdfs').exists()


def test_exact_size_limit_is_allowed(tmp_path):
    pdfs, imports = store(tmp_path, max_bytes=8)
    (imports / 'paper.pdf').write_bytes(b'%PDF-123')
    assert pdfs.import_pdf('paper.pdf').path.read_bytes() == b'%PDF-123'


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
