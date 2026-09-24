"""기본 맥락의 영속 저장·동시 생성·검색 진입점 분기를 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier
from types import SimpleNamespace

import pytest

from solo_leveling.application.evidence_qa.entry import SearchEntryService
from solo_leveling.domain.context import ContextNotReadyError
from solo_leveling.domain.evidence_qa import RetrievalMethod, SearchResult
from solo_leveling.domain.models import JobStatus, LearningContext, PaperVersion, ProcessingJob
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection, init_db
from solo_leveling.infrastructure.retrieval.sqlite_chroma import SQLiteContextReader
from tests.unit.test_learning_persistence import setup_db, publish


def test_default_context_persists_and_does_not_reuse_custom_context(setup_db):
    db, chunks = setup_db
    custom = publish(db, chunks)
    first = repo.get_or_create_default_context(db, 'v1')
    assert first.context_id != custom.context_id
    assert first.goal == 'understand'
    assert first.known_concepts == []
    assert first.embedding_set_id == 'idx'
    init_db(db)
    assert repo.get_or_create_default_context(db, 'v1') == first
    assert repo.get_learning_context(db, custom.context_id) == custom


def test_concurrent_default_context_creation_is_unique(setup_db):
    db, chunks = setup_db
    publish(db, chunks)
    barrier = Barrier(4)

    def prepare(_):
        barrier.wait(timeout=10)
        return repo.get_or_create_default_context(db, 'v1').context_id

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(prepare, range(4)))
    assert len(set(ids)) == 1
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM default_learning_contexts').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM learning_contexts').fetchone()[0] == 2


@pytest.mark.parametrize('status', [None, JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.FAILED, JobStatus.INTERRUPTED])
def test_unpublished_version_reports_job_without_creating_context(setup_db, status):
    db, _ = setup_db
    if status:
        repo.insert_job(db, ProcessingJob('job', 'v1', status=status))
    with pytest.raises(ContextNotReadyError) as caught:
        repo.get_or_create_default_context(db, 'v1')
    assert caught.value.status == status
    assert caught.value.job_id == ('job' if status else None)
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM learning_contexts').fetchone()[0] == 0


def test_default_context_mapping_cannot_silently_change_settings(setup_db):
    db, chunks = setup_db
    publish(db, chunks)
    context = repo.get_or_create_default_context(db, 'v1')
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE learning_contexts SET goal='skim' WHERE context_id=?", (context.context_id,))
    with pytest.raises(ValueError, match='기본 학습 맥락'):
        repo.get_or_create_default_context(db, 'v1')


def test_unknown_version_does_not_create_context(setup_db):
    db, _ = setup_db
    with pytest.raises(ValueError, match='논문 버전을 찾을 수 없습니다'):
        repo.get_or_create_default_context(db, 'missing')
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM learning_contexts').fetchone()[0] == 0


def test_default_contexts_are_separate_for_different_versions(setup_db):
    db, chunks = setup_db
    publish(db, chunks)
    first = repo.get_or_create_default_context(db, 'v1')
    # 다른 버전의 게시 색인을 독립적으로 구성한다.
    from solo_leveling.domain.models import Chunk, EmbeddingSet, ParseRevision
    from tests.unit.test_learning_persistence import make_batch

    repo.insert_version(db, PaperVersion('v2', 'paper', 'hash2', 'b.pdf', 'b.pdf'))
    repo.insert_parse_revision(db, ParseRevision('p2', 'v2', 1))
    other = [Chunk('c3', 'p2', 0, 'First.')]
    repo.insert_chunks(db, other)
    repo.save_translation_batch(db, make_batch(other, revision='t2'))
    repo.claim_ingestion_job(db, ProcessingJob('job2', 'v2'))
    repo.publish_search_index(db, 'v2', 'p2', EmbeddingSet('idx2', 't2', 'fake', 2), 'job2', 1, [])
    second = repo.get_or_create_default_context(db, 'v2')
    assert second.context_id != first.context_id
    assert second.embedding_set_id == 'idx2'


@pytest.fixture
def entry_parts():
    context = LearningContext('ctx', 'v', 'p', 't', embedding_set_id='idx')
    prepared, searches = [], []

    def prepare(version_id):
        prepared.append(version_id)
        return context

    def search(question, scope, top_k):
        searches.append(scope)
        return SearchResult(question, scope, RetrievalMethod.VECTOR, 'idx', ())

    store = SimpleNamespace(get_context=lambda key: context if key == 'ctx' else None,
                            get_or_create_default_context=prepare)
    return SearchEntryService(store, SimpleNamespace(search=search)), prepared, searches


def test_entry_prepares_default_only_when_context_is_omitted(entry_parts):
    service, prepared, searches = entry_parts
    result = service.search('질문', version_id='v', pdf_pages=(2,))
    assert result.context_id == 'ctx'
    assert result.result.scope.pdf_pages == (2,)
    assert prepared == ['v']
    service.search('질문', context_id='ctx')
    service.search('질문', context_id='ctx', version_id='v')
    assert prepared == ['v']
    assert len(searches) == 3


@pytest.mark.parametrize('kwargs', [{}, {'context_id': ''}, {'context_id': 'missing', 'version_id': 'v'},
    {'context_id': 'ctx', 'version_id': 'other'}, {'version_id': ''},
    {'version_id': 'v', 'top_k': 0}, {'version_id': 'v', 'pdf_pages': (0,)},
    {'version_id': 'v', 'pdf_pages': (1, 1)}, {'version_id': 'v', 'section_ids': ('',)}])
def test_invalid_entry_requests_do_not_create_or_search(entry_parts, kwargs):
    service, prepared, searches = entry_parts
    with pytest.raises(ValueError):
        service.search('질문', **kwargs)
    assert prepared == searches == []


def test_entry_does_not_search_while_registration_is_processing(setup_db):
    db, _ = setup_db
    repo.insert_job(db, ProcessingJob('job', 'v1', status=JobStatus.PROCESSING))
    searches = []
    service = SearchEntryService(SQLiteContextReader(db), SimpleNamespace(search=lambda *args: searches.append(args)))
    with pytest.raises(ContextNotReadyError):
        service.search('질문', version_id='v1')
    assert searches == []
