"""#232 위치 판정: 파일 접근 없이 검증·폴백·기존 근거 보호를 확인한다."""
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

from solo_leveling.application.evidence_qa.errors import (
    DataIntegrityError, InvalidArgumentError, ResourceNotFoundError,
)
from solo_leveling.application.evidence_qa.evidence import EvidenceService
from solo_leveling.application.paper_detail.navigation import PdfNavigationService
from solo_leveling.application.paper_detail.service import PaperDetailService
from solo_leveling.application.annotations.service import AnnotationService
from solo_leveling.domain.evidence_qa import EvidenceDetail, GetEvidenceResult
from solo_leveling.domain.models import Evidence
from solo_leveling.domain.paper_summary import SummaryItem
from solo_leveling.domain.pdf_navigation import (
    FileAccessResult, FileAccessStatus, NavigationReason, PdfNavigationTarget, PdfSourceIdentity,
)
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.generation.evidence_store import SQLiteEvidenceReader
from solo_leveling.infrastructure.retrieval.pdf_navigation import SQLitePdfSourceReader
from solo_leveling.infrastructure.retrieval.paper_detail import SQLitePaperDetailReader
from tests.unit.test_learning_persistence import setup_db, publish


class Sources:
    def __init__(self, source=None):
        self.source = source or PdfSourceIdentity('ctx', 'v1', 'p1', 'internal-file-hash', 7)
        self.calls = []

    def get(self, context_id):
        self.calls.append(context_id)
        return self.source


class EvidenceReader:
    def __init__(self):
        self.detail = EvidenceDetail('e1', 'c1', 'v1', 'p1', 't1', 'iv', 6,
                                     '저장 번역', 'stored quote', 'paper.pdf')
        self.calls = []

    def get(self, context_id, ids):
        self.calls.append((context_id, ids))
        return GetEvidenceResult((self.detail,))


def available(**changes):
    return replace(FileAccessResult(FileAccessStatus.AVAILABLE, 'v1', 'internal-file-hash', 7),
                   **changes)


@pytest.fixture
def navigation():
    reader, sources = EvidenceReader(), Sources()
    return PdfNavigationService(EvidenceService(reader), sources), reader, sources


def test_no_access_is_excerpt_only_and_does_not_open_files(navigation):
    service, reader, sources = navigation
    target = service.get('ctx', 'e1')
    assert target.to_dict() == {
        'version_id': 'v1', 'parse_revision_id': 'p1', 'pdf_page': 6,
        'printed_page_label': 'iv', 'navigation_level': 'excerpt_only',
        'coordinate_target': None, 'fallback': 'stored_excerpt',
        'reason_code': 'access_not_checked',
    }
    assert reader.calls == [('ctx', ('e1',))] and sources.calls == ['ctx']


def test_verified_file_enables_physical_page_without_coordinates(navigation):
    service, reader, _ = navigation
    target = service.get('ctx', 'e1', access=available())
    assert target.navigation_level == 'page' and target.reason_code is None
    assert target.pdf_page == 6 and target.printed_page_label == 'iv'
    assert target.to_dict()['coordinate_target'] is None
    # 저장 원문·번역이나 문자열 오프셋을 이동 좌표로 내보내지 않는다.
    output = repr(target.to_dict())
    for secret in ('internal-file-hash', 'stored quote', '저장 번역', 'stored_path', 'url', 'offset'):
        assert secret not in output
    assert reader.detail.quote_original == 'stored quote'


@pytest.mark.parametrize(('status', 'reason'), [
    (FileAccessStatus.MISSING, NavigationReason.FILE_MISSING),
    (FileAccessStatus.DENIED, NavigationReason.ACCESS_DENIED),
    (FileAccessStatus.EXPIRED, NavigationReason.ACCESS_EXPIRED),
    (FileAccessStatus.UNAVAILABLE, NavigationReason.ACCESS_UNAVAILABLE),
])
def test_access_failure_keeps_excerpt_and_can_recover_on_explicit_recheck(navigation, status, reason):
    service, reader, _ = navigation
    original = reader.detail
    failed = service.get('ctx', 'e1', access=FileAccessResult(status))
    assert failed.reason_code is reason and failed.navigation_level == 'excerpt_only'
    assert failed.to_dict()['fallback'] == 'stored_excerpt'
    assert reader.detail is original
    recovered = service.get('ctx', 'e1', access=available())
    assert recovered.navigation_level == 'page'


@pytest.mark.parametrize(('changes', 'reason'), [
    ({'version_id': 'different'}, NavigationReason.VERSION_MISMATCH),
    ({'file_hash': 'different'}, NavigationReason.FILE_HASH_MISMATCH),
])
def test_wrong_file_never_enables_page_navigation(navigation, changes, reason):
    service, _, _ = navigation
    target = service.get('ctx', 'e1', access=available(**changes))
    assert target.reason_code is reason and target.navigation_level == 'excerpt_only'
    assert target.pdf_page == 6  # 잘못된 파일에 맞춰 페이지를 보정하지 않는다.


@pytest.mark.parametrize('changes', [
    {'context_id': 'other'}, {'version_id': 'other'}, {'parse_revision_id': 'other'},
    {'page_count': 5},
])
def test_stored_connection_errors_are_integrity_errors(navigation, changes):
    service, reader, sources = navigation
    sources.source = replace(sources.source, **changes)
    with pytest.raises(DataIntegrityError, match='저장 출처 연결'):
        service.get('ctx', 'e1', access=available())
    assert reader.detail.quote_original == 'stored quote'


@pytest.mark.parametrize(('context', 'evidence', 'access'), [
    ('', 'e1', None), ('ctx', '', None), (None, 'e1', None),
    ('ctx', 'e1', {'status': 'available', 'stored_path': 'SECRET'}),
])
def test_bad_input_is_fixed_error_and_does_not_read(navigation, context, evidence, access):
    service, reader, sources = navigation
    with pytest.raises(InvalidArgumentError) as error:
        service.get(context, evidence, access=access)
    assert str(error.value) == 'PDF 위치 조회 입력이 올바르지 않습니다.'
    assert reader.calls == sources.calls == []


def test_existing_evidence_reference_check_is_preserved(navigation):
    service, reader, sources = navigation
    reader.detail = replace(reader.detail, evidence_id='other')
    with pytest.raises(DataIntegrityError):
        service.get('ctx', 'e1', access=available())
    assert sources.calls == ['ctx']


def test_wrong_projection_type_is_integrity_error(navigation):
    service, reader, sources = navigation
    sources.source = {'stored_path': 'SECRET', 'page_count': 7}
    with pytest.raises(DataIntegrityError) as error:
        service.get('ctx', 'e1', access=available())
    assert 'SECRET' not in str(error.value)
    assert reader.calls == []


@pytest.mark.parametrize('page_count', [0, -1, True, 2.5, '7', None])
def test_available_file_requires_valid_page_count(page_count):
    with pytest.raises(ValueError):
        available(page_count=page_count)


@pytest.mark.parametrize('changes', [{'version_id': ''}, {'file_hash': ''}])
def test_available_file_requires_identity(changes):
    with pytest.raises(ValueError):
        available(**changes)


def test_access_status_and_failed_metadata_are_not_guessed():
    with pytest.raises(ValueError):
        FileAccessResult('available', 'v1', 'hash', 7)
    with pytest.raises(ValueError):
        FileAccessResult(FileAccessStatus.DENIED, version_id='v1')
    assert 'internal-file-hash' not in repr(available())


@pytest.mark.parametrize('page', [0, -1, True, '6'])
def test_navigation_output_rejects_invalid_physical_page(page):
    with pytest.raises(ValueError):
        PdfNavigationTarget('v1', 'p1', page, None, None)


@pytest.fixture
def stored(setup_db):
    db, chunks = setup_db
    publish(db, chunks)
    repo.save_evidences(db, 'ctx', [Evidence('e1', 'c1', '첫째.', 'First.')])
    service = PdfNavigationService(EvidenceService(SQLiteEvidenceReader(db)), SQLitePdfSourceReader(db))
    return db, service


def test_real_projection_preserves_db_and_hides_paths(stored):
    db, service = stored
    before = Path(db).read_bytes()
    target = service.get('ctx', 'e1', access=FileAccessResult(FileAccessStatus.AVAILABLE, 'v1', 'hash', 2))
    assert target.navigation_level == 'page' and target.pdf_page == 1
    assert Path(db).read_bytes() == before
    output = target.to_dict()
    assert set(output) == {'version_id', 'parse_revision_id', 'pdf_page', 'printed_page_label',
                           'navigation_level', 'coordinate_target', 'fallback', 'reason_code'}
    assert '/private' not in repr(output) and 'hash' not in repr(output)


@pytest.mark.parametrize(('sql', 'params'), [
    ('UPDATE chunks SET original_text=? WHERE chunk_id=?', ('changed', 'c1')),
    ('UPDATE evidences SET quote_ko=? WHERE evidence_id=?', ('changed', 'e1')),
    ('UPDATE chunks SET pdf_page=? WHERE chunk_id=?', (3, 'c1')),
    ('UPDATE parse_revisions SET version_id=? WHERE parse_revision_id=?', ('other', 'p1')),
])
def test_real_stored_corruption_never_yields_navigation(stored, sql, params):
    db, service = stored
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(sql, params)
    before = Path(db).read_bytes()
    with pytest.raises(DataIntegrityError):
        service.get('ctx', 'e1', access=FileAccessResult(FileAccessStatus.AVAILABLE, 'v1', 'hash', 2))
    assert Path(db).read_bytes() == before


def test_foreign_context_and_missing_evidence_still_rejected(stored):
    db, service = stored
    repo.create_learning_context(db, replace(repo.get_learning_context(db, 'ctx'), context_id='other'))
    for context, evidence in [('other', 'e1'), ('ctx', 'missing'), ('absent', 'e1')]:
        with pytest.raises(ResourceNotFoundError):
            service.get(context, evidence)
    with pytest.raises(ResourceNotFoundError):
        SQLitePdfSourceReader(db).get('absent')


def test_access_is_compared_again_for_each_request(navigation):
    service, _, sources = navigation
    access = available()
    assert service.get('ctx', 'e1', access=access).navigation_level == 'page'
    sources.source = replace(sources.source, file_hash='changed-file')
    rejected = service.get('ctx', 'e1', access=access)
    assert rejected.navigation_level == 'excerpt_only'
    assert rejected.reason_code is NavigationReason.FILE_HASH_MISMATCH
    assert len(sources.calls) == 2


@pytest.mark.parametrize(('column', 'value'), [('page_count', 0), ('file_hash', '')])
def test_projection_rejects_bad_saved_metadata(stored, column, value):
    db, service = stored
    with closing(sqlite3.connect(db)) as conn, conn:
        if column == 'page_count':
            conn.execute('UPDATE parse_revisions SET page_count=?', (value,))
        else:
            conn.execute('UPDATE paper_versions SET file_hash=?', (value,))
    with pytest.raises(DataIntegrityError) as error:
        service.get('ctx', 'e1')
    assert str(error.value) == 'PDF 위치의 저장 정보를 확인하지 못했습니다.'


def test_projection_does_not_create_missing_db_or_expose_errors(tmp_path):
    db = tmp_path / 'SECRET.sqlite'
    with pytest.raises(DataIntegrityError) as error:
        SQLitePdfSourceReader(str(db)).get('ctx')
    assert str(error.value) == 'PDF 위치의 저장 정보를 확인하지 못했습니다.'
    assert not db.exists() and 'SECRET' not in str(error.value)


def test_service_does_not_create_missing_db_or_expose_errors(tmp_path):
    db = tmp_path / 'SECRET.sqlite'
    service = PdfNavigationService(EvidenceService(SQLiteEvidenceReader(str(db))),
                                   SQLitePdfSourceReader(str(db)))
    with pytest.raises(DataIntegrityError) as error:
        service.get('ctx', 'e1')
    assert not db.exists()
    assert 'SECRET' not in str(error.value)


@pytest.mark.parametrize('page_count', [5, 8])
def test_access_page_inconsistency_is_integrity_error(navigation, page_count):
    service, reader, _ = navigation
    original = reader.detail
    with pytest.raises(DataIntegrityError):
        service.get('ctx', 'e1', access=available(page_count=page_count))
    assert reader.detail is original


def test_navigation_fallback_and_integrity_error_preserve_detail_and_notes(stored):
    db, service = stored
    repo.save_summary(db, 'ctx', 'paper', [SummaryItem('claim', '저장 주장', ('e1',))])
    notes = AnnotationService(db)
    notes.create('ctx', 'note', '보존할 메모')
    detail = PaperDetailService(SQLitePaperDetailReader(db), notes)
    expected, expected_notes = detail.get('ctx'), notes.list('ctx')
    before = Path(db).read_bytes()
    for access in [None, FileAccessResult(FileAccessStatus.DENIED),
                   FileAccessResult(FileAccessStatus.AVAILABLE, 'other', 'hash', 2)]:
        assert service.get('ctx', 'e1', access=access).navigation_level == 'excerpt_only'
        assert detail.get('ctx') == expected and notes.list('ctx') == expected_notes
    with pytest.raises(DataIntegrityError):
        service.get('ctx', 'e1', access=FileAccessResult(FileAccessStatus.AVAILABLE, 'v1', 'hash', 3))
    assert detail.get('ctx') == expected and notes.list('ctx') == expected_notes
    assert Path(db).read_bytes() == before
