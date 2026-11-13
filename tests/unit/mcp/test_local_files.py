"""로컬 PDF 가져오기 경계와 관리 복사본을 검증한다."""

from io import BytesIO
import errno
import os
from pathlib import Path
import subprocess

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


def test_inspect_validates_and_fingerprints_without_copying(tmp_path):
    pdfs, imports = store(tmp_path)
    source = imports / 'paper.pdf'
    source.write_bytes(b'%PDF-1.7\ntest')
    inspected = pdfs.inspect_pdf('paper.pdf')
    imported = pdfs.import_pdf('paper.pdf')
    assert inspected.original_filename == imported.original_filename
    assert inspected.file_hash == imported.file_hash
    assert inspected.input_fingerprint == imported.input_fingerprint
    assert imported.path.is_file()


@pytest.mark.parametrize('path', ['../paper.pdf', '/paper.pdf', 'missing.pdf', 'paper.txt'])
def test_inspect_rejects_invalid_source_without_managed_copy(tmp_path, path):
    pdfs, imports = store(tmp_path)
    (imports / 'paper.txt').write_bytes(b'%PDF-1.7')
    with pytest.raises(InvalidArgumentError):
        pdfs.inspect_pdf(path)
    assert not (tmp_path / 'pdfs').exists()


@pytest.mark.parametrize('content', [b'%PDF-' + b'x' * 65532, b'%PDF', b''],
                         ids=['over-limit', 'short-header', 'empty'])
def test_inspect_checks_actual_bytes_after_open(tmp_path, monkeypatch, content):
    pdfs, imports = store(tmp_path, max_bytes=65536)
    (imports / 'paper.pdf').write_bytes(b'%PDF-original')
    # 초기 크기 검사 뒤 실제로 읽히는 내용이 달라지는 경우를 재현한다.
    monkeypatch.setattr(pdfs, '_open_source', lambda source: BytesIO(content))
    with pytest.raises(UnsupportedDocumentError):
        pdfs.inspect_pdf('paper.pdf')
    assert not (tmp_path / 'pdfs').exists()


def test_inspect_rejects_non_pdf_content_without_managed_copy(tmp_path):
    pdfs, imports = store(tmp_path)
    (imports / 'paper.pdf').write_bytes(b'not-a-pdf')
    with pytest.raises(UnsupportedDocumentError):
        pdfs.inspect_pdf('paper.pdf')
    assert not (tmp_path / 'pdfs').exists()


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


@pytest.mark.parametrize(('name', 'content', 'error'), [
    ('paper.txt', b'%PDF-1.7', InvalidArgumentError),
    ('paper.pdf', b'not-a-pdf', UnsupportedDocumentError),
])
def test_invalid_documents_are_rejected(tmp_path, name, content, error):
    pdfs, imports = store(tmp_path)
    (imports / name).write_bytes(content)
    with pytest.raises(error):
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


def test_hardlinked_pdf_is_rejected_before_copy(tmp_path):
    import os
    pdfs, imports = store(tmp_path)
    outside = tmp_path / 'outside.pdf'
    outside.write_bytes(b'%PDF-private')
    os.link(outside, imports / 'paper.pdf')
    with pytest.raises(InvalidArgumentError):
        pdfs.import_pdf('paper.pdf')
    assert not (tmp_path / 'pdfs').exists()
    assert outside.read_bytes() == b'%PDF-private'


def test_internal_symlink_still_imports_resolved_file(tmp_path):
    pdfs, imports = store(tmp_path)
    (imports / 'original.pdf').write_bytes(b'%PDF-original')
    symlink_or_skip(imports / 'alias.pdf', imports / 'original.pdf')
    assert pdfs.import_pdf('alias.pdf').path.read_bytes() == b'%PDF-original'


@pytest.mark.skipif(os.name != 'nt', reason='Windows 정션 검증')
@pytest.mark.parametrize('after_validation', [False, True], ids=['existing', 'after_validation'])
def test_windows_external_junction_creates_no_copy(tmp_path, monkeypatch, after_validation):
    pdfs, imports = store(tmp_path)
    slot, outside = imports / 'slot', tmp_path / 'outside'
    slot.mkdir()
    outside.mkdir()
    (slot / 'sample.pdf').write_bytes(b'%PDF-inside!')
    (outside / 'sample.pdf').write_bytes(b'%PDF-outside')
    original = pdfs._open_source

    def swap():
        slot.rename(imports / 'saved')
        subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(slot), str(outside)],
                       check=True, capture_output=True, timeout=10)
        assert slot.resolve() == outside.resolve()

    def swap_then_open(source):
        swap()
        return original(source)

    try:
        if after_validation:
            monkeypatch.setattr(pdfs, '_open_source', swap_then_open)
        else:
            swap()
        with pytest.raises(InvalidArgumentError):
            pdfs.import_pdf('slot/sample.pdf')
        assert slot.resolve() == outside.resolve()
        assert not list((tmp_path / 'pdfs').rglob('*'))
    finally:
        if slot.exists() and slot.resolve() == outside.resolve():
            slot.rmdir()


@pytest.mark.skipif(os.name != 'nt', reason='Windows 디렉터리 핸들 검증')
def test_windows_parent_cannot_be_replaced_at_final_open(tmp_path, monkeypatch):
    from solo_leveling.infrastructure.storage import windows_pdf

    pdfs, imports = store(tmp_path)
    slot = imports / 'slot'
    slot.mkdir()
    (slot / 'sample.pdf').write_bytes(b'%PDF-inside!')
    original = windows_pdf._open_component
    attempts = []

    def attempt_rename(path, *, directory):
        if not directory:
            with pytest.raises(PermissionError) as denied:
                slot.rename(imports / 'saved')
            attempts.append(denied.value.winerror)
        return original(path, directory=directory)

    monkeypatch.setattr(windows_pdf, '_open_component', attempt_rename)
    result = pdfs.import_pdf('slot/sample.pdf')
    assert len(attempts) == 1 and attempts[0] in (5, 32)
    assert result.path.read_bytes() == b'%PDF-inside!'
    slot.rename(imports / 'saved')


@pytest.mark.skipif(os.name != 'posix', reason='POSIX 디렉터리 핸들 검증')
@pytest.mark.parametrize('replacement', ['file_link', 'directory_link', 'fifo', 'hardlink'])
def test_replacement_after_validation_is_rejected_before_copy(tmp_path, monkeypatch, replacement):
    import os
    pdfs, imports = store(tmp_path)
    nested = imports / 'nested'
    nested.mkdir()
    source = nested / 'paper.pdf'
    source.write_bytes(b'%PDF-safe')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'paper.pdf').write_bytes(b'%PDF-private')
    original_open = pdfs._open_source

    def replace_then_open(path):
        if replacement == 'directory_link':
            nested.rename(imports / 'previous')
            nested.symlink_to(outside, target_is_directory=True)
        else:
            source.unlink()
            if replacement == 'file_link':
                source.symlink_to(outside / 'paper.pdf')
            elif replacement == 'fifo':
                os.mkfifo(source)
            else:
                os.link(outside / 'paper.pdf', source)
        return original_open(path)

    monkeypatch.setattr(pdfs, '_open_source', replace_then_open)
    with pytest.raises(InvalidArgumentError):
        pdfs.import_pdf('nested/paper.pdf')
    assert not (tmp_path / 'pdfs').exists()


def test_opened_file_size_is_rechecked(tmp_path, monkeypatch):
    pdfs, imports = store(tmp_path, max_bytes=10)
    source = imports / 'paper.pdf'
    source.write_bytes(b'%PDF-safe')
    original_open = pdfs._open_source

    def grow_then_open(path):
        source.write_bytes(b'%PDF-' + b'x' * 20)
        return original_open(path)

    monkeypatch.setattr(pdfs, '_open_source', grow_then_open)
    with pytest.raises(UnsupportedDocumentError):
        pdfs.import_pdf('paper.pdf')
    assert not (tmp_path / 'pdfs').exists()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX 디렉터리 핸들 검증')
def test_open_directory_handle_stays_on_original_directory_after_replacement(tmp_path, monkeypatch):
    pdfs, imports = store(tmp_path)
    nested = imports / 'nested'
    nested.mkdir()
    (nested / 'paper.pdf').write_bytes(b'%PDF-safe')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'paper.pdf').write_bytes(b'%PDF-private')
    original_open = os.open

    def replace_parent(path, flags, *args, **kwargs):
        if path == 'paper.pdf' and kwargs.get('dir_fd') is not None:
            nested.rename(imports / 'previous')
            nested.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, 'open', replace_parent)
    result = pdfs.import_pdf('nested/paper.pdf')
    assert result.path.read_bytes() == b'%PDF-safe'


@pytest.mark.skipif(os.name != 'posix', reason='POSIX 파일·디렉터리 핸들 검증')
@pytest.mark.parametrize('outcome', ['success', 'rejected', 'copy_failed'])
def test_import_closes_opened_descriptors_on_every_exit(tmp_path, monkeypatch, outcome):
    from solo_leveling.infrastructure.storage.pdf_store import PdfStore

    pdfs, imports = store(tmp_path)
    (imports / 'nested').mkdir()
    source = imports / 'nested' / 'paper.pdf'
    source.write_bytes(b'%PDF-safe')
    if outcome == 'rejected':
        os.link(source, imports / 'other.pdf')
    descriptors = []
    original_open = os.open

    def track_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    def fail_copy(*args, **kwargs):
        raise OSError('합성 복사 실패')

    monkeypatch.setattr(os, 'open', track_open)
    if outcome == 'copy_failed':
        monkeypatch.setattr(PdfStore, 'save_stream', fail_copy)
        with pytest.raises(OSError, match='합성 복사 실패'):
            pdfs.import_pdf('nested/paper.pdf')
    elif outcome == 'rejected':
        with pytest.raises(InvalidArgumentError):
            pdfs.import_pdf('nested/paper.pdf')
    else:
        pdfs.import_pdf('nested/paper.pdf')
    assert len(descriptors) == 3
    for descriptor in descriptors:
        with pytest.raises(OSError) as caught:
            os.fstat(descriptor)
        assert caught.value.errno == errno.EBADF
