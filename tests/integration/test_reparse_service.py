"""원본 데이터/API 없이 실제 파서·SQLite·Chroma로 재파싱 실행을 검증한다."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from tests.fixtures.pdf_builder import write_minimal_pdf
from solo_leveling.application.annotations.service import AnnotationService
from solo_leveling.application.evidence_qa.search import ContextSearchService
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.application.translation.ports import TranslationProviderUnavailable, ProviderFailure
from solo_leveling.application.translation.retry import TranslationRetryPolicy
from solo_leveling.domain.annotation import SegmentInput
from solo_leveling.domain.models import (
    Chunk, Paper, PaperVersion, ParseRevision, ProcessingJob, JobStatus, JobStage,
    EmbeddingSet, Evidence, ReparseDisposition as S,
    PaperSection,
)
from solo_leveling.domain.paper_summary import SummaryItem
from solo_leveling.domain.reparse import ReparseExecutionError
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import init_db
from solo_leveling.infrastructure.parsing.pdf_extractor import ExtractedPage, extract_pages
from solo_leveling.infrastructure.parsing.sections import PageHeading
from solo_leveling.infrastructure.retrieval.sqlite_chroma import SQLiteContextReader, SQLiteChromaRetriever
from solo_leveling.infrastructure.storage.vector_store import upsert_chunk_embeddings
from solo_leveling.workers.reparse import ReparseService
from solo_leveling.workers import reparse as module


SETTINGS = TranslationSettings('synthetic', 'synthetic-v1', 'prompt-v1')


@pytest.fixture(autouse=True)
def no_external_api(monkeypatch):
    import requests
    import httpx
    def disabled(*args, **kwargs):
        raise AssertionError('REAL_API_DISABLED')
    async def async_disabled(*args, **kwargs):
        disabled()
    monkeypatch.setenv('ANONYMIZED_TELEMETRY', 'False')
    monkeypatch.setattr(requests.sessions.Session, 'request', disabled)
    monkeypatch.setattr(httpx.Client, 'send', disabled)
    monkeypatch.setattr(httpx.AsyncClient, 'send', async_disabled)


class Provider:
    def __init__(self, empty=(), callback=None):
        self.calls, self.empty, self.callback = [], empty, callback

    def translate(self, request, settings):
        self.calls.append(request)
        if self.callback:
            self.callback()
        return '' if any(text in request.original_text for text in self.empty) else '번역 ' + request.original_text


class Embedder:
    def __init__(self, callback=None, vectors=None):
        self.calls, self.callback, self.vectors = [], callback, vectors

    def __call__(self, texts, *, model_name):
        self.calls.append((list(texts), model_name))
        if self.callback:
            self.callback()
        return self.vectors if self.vectors is not None else [[1., 0., 0., 0.] for _ in texts]


@pytest.fixture
def data(tmp_path, chroma_client_factory):
    pdf_root = tmp_path / 'pdfs'
    pdf_root.mkdir()
    pdf = pdf_root / 'source.pdf'
    write_minimal_pdf(str(pdf), ['New first sentence.', 'New second sentence.'])
    path = str(tmp_path / 'test.sqlite3')
    init_db(path)
    repo.insert_paper(path, Paper('paper'))
    repo.insert_version(path, PaperVersion('v1', 'paper', hashlib.sha256(pdf.read_bytes()).hexdigest(), str(pdf), 'source.pdf'))
    repo.insert_parse_revision(path, ParseRevision('p1', 'v1', 2))
    chunks = [Chunk('old-1', 'p1', 0, 'Old first.', pdf_page=1),
              Chunk('old-2', 'p1', 1, 'Old second.', pdf_page=2)]
    repo.insert_chunks(path, chunks)
    batch = TranslationService(Provider(), lambda: 't1').translate(chunks, SETTINGS)
    repo.save_translation_batch(path, batch)
    client = chroma_client_factory(str(tmp_path / 'chroma'))
    upsert_chunk_embeddings(client, [c.chunk_id for c in chunks], [[1., 0., 0., 0.]] * 2,
                            'e1', 'paper', 'v1', [i.text for i in batch.items])
    repo.insert_job(path, ProcessingJob('j1', 'v1', JobStatus.PROCESSING, JobStage.INDEX))
    repo.publish_search_index(path, 'v1', 'p1', EmbeddingSet('e1', 't1', 'synthetic-4', 4),
                              'j1', [c.chunk_id for c in chunks], [])
    old = repo.get_or_create_default_context(path, 'v1')
    repo.save_evidences(path, old.context_id, [Evidence('old-evidence', 'old-1', '번역 Old first.', 'Old first.')])
    repo.save_summary(path, old.context_id, 'paper', [SummaryItem('claim', '이전 주장', ('old-evidence',))])
    AnnotationService(path).create(old.context_id, 'note', '이전 메모')
    AnnotationService(path).create(old.context_id, 'highlight', '',
        segments=[SegmentInput('old-1', 0, 3, 'Old')])
    assert {item.kind for item in AnnotationService(path).list(old.context_id).items} == {'note', 'highlight'}
    return SimpleNamespace(db=path, pdf=pdf, root=pdf_root, client=client, old=old, tmp=tmp_path)


def service(data, **options):
    provider = options.pop('provider', Provider())
    embedder = options.pop('embedder', Embedder())
    result = ReparseService(data.db, str(data.tmp / 'chroma'), data.root,
        translation_service=options.pop('translation_service', TranslationService(provider)),
        translation_settings=options.pop('translation_settings', SETTINGS), embedder=embedder,
        embedding_model='synthetic-4', provider_available=options.pop('provider_available', True),
        chroma_client=data.client, **options)
    return result, provider, embedder


def prepare(svc, key='request'):
    return svc.prepare('v1', 'p1', 'e1', key)


def lookup(svc, key='request'):
    return svc.lookup('v1', 'p1', 'e1', key)


def old_state(data):
    return (repo.get_learning_context(data.db, data.old.context_id),
            repo.get_evidences(data.db, data.old.context_id, ['old-evidence']),
            repo.get_summary(data.db, data.old.context_id),
            AnnotationService(data.db).list(data.old.context_id),
            repo.get_chunks_by_parse_revision(data.db, 'p1'),
            repo.get_search_index(data.db, 'v1', embedding_set_id='e1'))


def count(data, table):
    with closing(repo.get_connection(data.db)) as conn:
        return conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]


def test_actual_pdf_full_flow_preserves_old_context_and_search(data):
    svc, provider, embedder = service(data)
    before, pdf_bytes = old_state(data), data.pdf.read_bytes()
    job = prepare(svc)
    assert job.status == S.ACCEPTED and not provider.calls and not embedder.calls
    assert lookup(svc).status == S.IN_PROGRESS
    result = svc.run(job.job_id)
    assert result.status == S.COMPLETED and result.result_available
    assert result.chunk_count == 2 and result.failed_count == 0
    assert result.new_parse_revision_id != 'p1' and result.translation_revision_id != 't1'
    assert result.new_embedding_set_id != 'e1'
    assert len(provider.calls) == 2 and len(embedder.calls) == 1
    # 기본 여부 전환만 제외하면 기존 게시 데이터와 맥락은 그대로다.
    after = old_state(data)
    assert after[:-1] == before[:-1]
    assert {k: v for k, v in after[-1].items() if k != 'is_default'} == {
        k: v for k, v in before[-1].items() if k != 'is_default'}
    assert data.pdf.read_bytes() == pdf_bytes
    assert count(data, 'learning_contexts') == 1
    new = repo.get_or_create_default_context(data.db, 'v1')
    assert new.embedding_set_id == result.new_embedding_set_id
    search = ContextSearchService(SQLiteContextReader(data.db), SQLiteChromaRetriever(
        data.db, data.client, embedder=Embedder()))
    old_hits = search.search('합성 질문', data.old.context_id, 5).items
    new_hits = search.search('합성 질문', new.context_id, 5).items
    assert {h.chunk.chunk_id for h in old_hits} == {'old-1', 'old-2'}
    assert all(h.chunk.parse_revision_id == result.new_parse_revision_id for h in new_hits)
    assert {h.chunk.original_text for h in new_hits} == {'New first sentence.', 'New second sentence.'}
    assert {h.chunk.translation_revision_id for h in new_hits} == {result.translation_revision_id}


def test_unknown_lookup_is_read_only_and_missing_database_is_not_created(data):
    svc, provider, embedder = service(data)
    before = Path(data.db).read_bytes()
    assert lookup(svc) is None and lookup(svc) is None
    assert Path(data.db).read_bytes() == before
    assert not provider.calls and not embedder.calls
    missing = data.tmp / 'missing.db'
    with pytest.raises(ReparseExecutionError, match='DATABASE_UNAVAILABLE'):
        ReparseService(missing, data.tmp, data.root, translation_service=None,
                       translation_settings=SETTINGS, embedder=Embedder(), embedding_model='synthetic')
    assert not missing.exists()


def test_replay_duplicate_keys_and_concurrent_execution_generate_once(data):
    svc, provider, embedder = service(data)
    with ThreadPoolExecutor(max_workers=2) as pool:
        registered = list(pool.map(lambda key: prepare(svc, key), ['request', 'same-input']))
    assert {r.status for r in registered} == {S.ACCEPTED, S.IN_PROGRESS}
    assert len({r.job_id for r in registered}) == 1
    job = registered[0].job_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(svc.run, [job, job]))
    result = svc.run(job)
    assert result.status == S.COMPLETED
    assert prepare(svc) == replace(result, status=S.COMPLETED)
    assert svc.lookup('v1', 'p1', 'e1', 'same-input') == result
    assert len(provider.calls) == 2 and len(embedder.calls) == 1
    assert count(data, 'processing_jobs') == 2


def test_changed_inputs_are_not_linked_to_other_jobs(data):
    svc, provider, embedder = service(data)
    accepted = prepare(svc)
    conflicting, _, _ = service(data, translation_settings=replace(SETTINGS, model='other'))
    assert prepare(conflicting).status == S.REQUEST_CONFLICT
    busy = prepare(conflicting, 'other-input')
    assert busy.status == S.IN_PROGRESS and busy.job_id is None
    assert lookup(conflicting, 'other-input') is None
    wrong = svc.lookup('other-version', 'p1', 'e1', 'request')
    assert wrong.status == S.REQUEST_CONFLICT and wrong.job_id is None
    assert not provider.calls and not embedder.calls
    assert count(data, 'processing_jobs') == 2
    assert svc.run(accepted.job_id).status == S.COMPLETED


def test_read_only_lookup_after_restart_never_resumes_work(data):
    svc, provider, embedder = service(data)
    accepted = prepare(svc)
    repo.mark_interrupted_jobs_on_startup(data.db)
    restarted, new_provider, new_embedder = service(data)
    assert lookup(restarted).status == S.INTERRUPTED
    with pytest.raises(ReparseExecutionError, match='NOT_OWNED'):
        restarted.run(accepted.job_id)
    assert svc.run(accepted.job_id).status == S.INTERRUPTED
    assert not provider.calls and not embedder.calls and not new_provider.calls and not new_embedder.calls


@pytest.mark.parametrize('mode', ['missing', 'changed', 'outside'])
def test_file_errors_do_not_parse_or_translate(data, mode):
    calls = []
    svc, provider, embedder = service(data, parser=lambda path: calls.append(path))
    if mode == 'missing':
        data.pdf.unlink()
    elif mode == 'changed':
        data.pdf.write_bytes(b'changed synthetic file')
    else:
        (data.tmp / 'outside.pdf').write_bytes(data.pdf.read_bytes())
        with closing(repo.get_connection(data.db)) as conn, conn:
            conn.execute('UPDATE paper_versions SET stored_path=? WHERE version_id=?',
                         (str(data.tmp / 'outside.pdf'), 'v1'))
    result = svc.run(prepare(svc).job_id)
    assert result.status == S.FAILED
    assert result.reason_code == ('REPARSE_FILE_MISMATCH' if mode == 'changed' else 'REPARSE_FILE_UNAVAILABLE')
    assert not calls and not provider.calls and not embedder.calls
    assert repo.get_search_index(data.db, 'v1')['embedding_set_id'] == 'e1'


def test_link_swap_before_snapshot_open_blocks_translation_and_publication(data, monkeypatch):
    outside = data.tmp / 'outside.pdf'
    outside.write_bytes(data.pdf.read_bytes())
    link = data.tmp / 'link.pdf'
    try:
        link.symlink_to(outside)
    except OSError as error:
        if getattr(error, 'winerror', None) == 1314:
            pytest.skip('심볼릭 링크 생성 권한이 필요합니다.')
        raise
    calls = []
    def parser(path):
        calls.append(path)
        return extract_pages(path)
    svc, provider, embedder = service(data, parser=parser)
    job = prepare(svc)
    make_directory = module.tempfile.TemporaryDirectory
    def swap_before_copy(*args, **kwargs):
        link.replace(data.pdf)
        return make_directory(*args, **kwargs)
    monkeypatch.setattr(module.tempfile, 'TemporaryDirectory', swap_before_copy)
    result = svc.run(job.job_id)
    assert data.pdf.is_symlink()
    assert result.status == S.FAILED and result.reason_code == 'REPARSE_FILE_UNAVAILABLE'
    assert not calls and not provider.calls and not embedder.calls
    assert count(data, 'search_indexes') == 1
    assert repo.get_search_index(data.db, 'v1')['embedding_set_id'] == 'e1'


def test_parser_reads_verified_snapshot_not_mutable_original(data):
    def parser(path):
        assert Path(path) != data.pdf
        data.pdf.write_bytes(b'changed after verified snapshot')
        return extract_pages(path)
    svc, _, _ = service(data, parser=parser)
    result = svc.run(prepare(svc).job_id)
    assert result.status == S.COMPLETED
    chunks = repo.get_chunks_by_parse_revision(data.db, result.new_parse_revision_id)
    assert chunks[0].original_text == 'New first sentence.'


@pytest.mark.parametrize('pages', [[], [ExtractedPage(1, '')]])
def test_empty_extraction_preserves_previous_service(data, pages):
    before = old_state(data)
    svc, provider, embedder = service(data, parser=lambda path: pages)
    result = svc.run(prepare(svc).job_id)
    assert result.status == S.FAILED and result.reason_code == 'REPARSE_EMPTY_EXTRACTION'
    assert not provider.calls and not embedder.calls
    assert old_state(data) == before


@pytest.mark.parametrize('allow_partial,empty,status', [
    (False, ('New first',), S.FAILED), (True, ('New first',), S.COMPLETED),
    (True, ('New first', 'New second'), S.FAILED),
])
def test_partial_policy_is_explicit_and_zero_success_never_publishes(data, allow_partial, empty, status):
    svc, provider, embedder = service(data, provider=Provider(empty), allow_partial=allow_partial)
    result = svc.run(prepare(svc).job_id)
    assert result.status == status
    if status == S.COMPLETED:
        assert result.chunk_count == 1 and result.failed_count == 1
        job = repo.get_job(data.db, result.job_id)
        assert any(value.startswith('translation_failed:') for value in job.limitations)
    else:
        assert not embedder.calls
        assert repo.get_search_index(data.db, 'v1')['embedding_set_id'] == 'e1'


@pytest.mark.parametrize('vectors', [[], [[1.]], [[True], [1.]], [[float('nan')], [1.]], [[1.], [1., 2.]], [[], []]])
def test_invalid_vectors_do_not_publish_or_touch_old_collection(data, vectors):
    svc, _, _ = service(data, embedder=Embedder(vectors=vectors))
    result = svc.run(prepare(svc).job_id)
    assert result.status == S.FAILED and result.reason_code == 'REPARSE_INVALID_EMBEDDING'
    assert [c.name for c in data.client.list_collections()] == ['chunks-e1']


@pytest.mark.parametrize('point', ['parser', 'translation', 'embedding', 'vector_write'])
def test_interruption_cannot_publish_late_results(data, monkeypatch, point):
    interrupt = lambda: repo.mark_interrupted_jobs_on_startup(data.db)
    options = {}
    if point == 'parser':
        def parser(path):
            pages = extract_pages(path)
            interrupt()
            return pages
        options['parser'] = parser
    elif point == 'translation':
        options['provider'] = Provider(callback=interrupt)
    elif point == 'embedding':
        options['embedder'] = Embedder(callback=interrupt)
    else:
        original = module.upsert_chunk_embeddings
        def write(*args, **kwargs):
            original(*args, **kwargs)
            interrupt()
        monkeypatch.setattr(module, 'upsert_chunk_embeddings', write)
    before = old_state(data)
    svc, provider, _ = service(data, **options)
    result = svc.run(prepare(svc).job_id)
    assert result.status == S.INTERRUPTED and not result.result_available
    assert old_state(data) == before
    assert count(data, 'search_indexes') == 1
    if point == 'translation':
        assert len(provider.calls) == 1


def test_publish_response_loss_replays_exact_completed_index(data, monkeypatch):
    original = repo.publish_reparsed_index
    def publish(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('SYNTHETIC_PRIVATE_RESPONSE_LOSS')
    monkeypatch.setattr(repo, 'publish_reparsed_index', publish)
    svc, provider, embedder = service(data)
    accepted = prepare(svc)
    result = svc.run(accepted.job_id)
    assert result.status == S.COMPLETED and lookup(svc) == result
    assert svc.run(accepted.job_id) == result
    assert len(provider.calls) == 2 and len(embedder.calls) == 1
    # 이후 다른 작업이 기본을 바꿔도 완료 응답은 해당 작업의 색인으로 고정된다.
    with closing(repo.get_connection(data.db)) as conn, conn:
        conn.execute('UPDATE search_indexes SET is_default=0 WHERE embedding_set_id=?', (result.new_embedding_set_id,))
        conn.execute("UPDATE search_indexes SET is_default=1 WHERE embedding_set_id='e1'")
    assert lookup(svc) == result


def test_base_change_before_publish_retains_winner_and_old_data(data, monkeypatch):
    original = repo.publish_reparsed_index
    def publish(db, job_id, embedding, ids, limitations):
        with closing(repo.get_connection(db)) as conn, conn:
            conn.execute("UPDATE search_indexes SET is_default=0 WHERE embedding_set_id='e1'")
        original(db, job_id, embedding, ids, limitations)
    monkeypatch.setattr(repo, 'publish_reparsed_index', publish)
    svc, _, _ = service(data)
    result = svc.run(prepare(svc).job_id)
    assert result.status == S.FAILED and not result.result_available
    assert count(data, 'search_indexes') == 1
    assert repo.get_search_index(data.db, 'v1', embedding_set_id='e1') is not None
    assert repo.get_evidences(data.db, data.old.context_id, ['old-evidence']).evidence


def test_provider_disabled_never_accepts_new_work_but_can_replay_completed(data):
    svc, provider, embedder = service(data, provider_available=False)
    with pytest.raises(ReparseExecutionError, match='PROVIDER_UNAVAILABLE'):
        prepare(svc)
    assert count(data, 'processing_jobs') == 1 and not provider.calls and not embedder.calls
    svc.provider_available = True
    completed = svc.run(prepare(svc).job_id)
    svc.provider_available = False
    assert prepare(svc) == completed


def test_arbitrary_exception_is_not_stored_or_returned(data, capsys):
    def fail(path):
        raise ValueError('SYNTHETIC_PRIVATE_PDF_TEXT_AND_PATH')
    svc, _, _ = service(data, parser=fail)
    result = svc.run(prepare(svc).job_id)
    assert result.reason_code == 'REPARSE_FAILED'
    assert 'SYNTHETIC_PRIVATE' not in repr(result)
    assert 'SYNTHETIC_PRIVATE' not in repr(repo.get_job(data.db, result.job_id))
    assert 'SYNTHETIC_PRIVATE' not in str(capsys.readouterr())


def test_translation_retry_policy_is_preserved_without_adding_outer_retries(data):
    class Transient(Provider):
        def translate(self, request, settings):
            self.calls.append(request)
            if len(self.calls) == 1:
                raise TranslationProviderUnavailable(code=ProviderFailure.TIMEOUT)
            return '번역 ' + request.original_text
    provider = Transient()
    translation = TranslationService(provider, retry_policy=TranslationRetryPolicy(
        max_attempts=2, base_delay_seconds=0, jitter_seconds=0), sleep=lambda delay: None)
    svc, _, _ = service(data, translation_service=translation)
    result = svc.run(prepare(svc).job_id)
    assert result.status == S.COMPLETED and len(provider.calls) == 3
    assert provider.calls[0].chunk_id == provider.calls[1].chunk_id


@pytest.mark.parametrize('kind', ['revision', 'source', 'settings', 'missing_item'])
def test_invalid_translation_batch_is_not_saved(data, monkeypatch, kind):
    svc, _, _ = service(data)
    original = svc.translation.with_revision_id
    def with_revision(revision_id):
        translation = original(revision_id)
        translate = translation.translate
        def changed(chunks, settings):
            batch = translate(chunks, settings)
            if kind == 'revision':
                batch.revision.parse_revision_id = 'p1'
            elif kind == 'source':
                item = batch.items[0]
                batch = replace(batch, items=(replace(item, request=replace(item.request, original_text='another source')),
                                              *batch.items[1:]))
            elif kind == 'settings':
                batch = replace(batch, settings=replace(batch.settings, prompt_version='other-prompt'))
            else:
                batch = replace(batch, items=batch.items[:1])
            return batch
        translation.translate = changed
        return translation
    monkeypatch.setattr(svc.translation, 'with_revision_id', with_revision)
    result = svc.run(prepare(svc).job_id)
    assert result.status == S.FAILED
    assert count(data, 'translation_revisions') == 1
    assert repo.get_translated_chunks(data.db, 'p1', 't1')[0].original_text == 'Old first.'


@pytest.mark.parametrize('corruption', ['metadata', 'document', 'missing_vector'])
def test_vector_readback_must_match_before_publish(data, monkeypatch, corruption):
    original = module.upsert_chunk_embeddings
    def corrupt(client, ids, vectors, index_id, paper_id, version_id, docs, **kwargs):
        original(client, ids, vectors, index_id, paper_id, version_id, docs, **kwargs)
        collection = client.get_collection(kwargs['collection_name'])
        if corruption == 'metadata':
            collection.update(ids=[ids[0]], metadatas=[{'version_id':'wrong-version'}])
        elif corruption == 'document':
            collection.update(ids=[ids[0]], embeddings=[vectors[0]], documents=['wrong document'])
        else:
            collection.delete(ids=[ids[0]])
    monkeypatch.setattr(module, 'upsert_chunk_embeddings', corrupt)
    before = old_state(data)
    svc, _, _ = service(data)
    result = svc.run(prepare(svc).job_id)
    assert result.status == S.FAILED and not result.result_available
    assert old_state(data) == before
    # 확인되지 않은 컬렉션을 자동 삭제하지 않으며 기존 컬렉션을 보존한다.
    assert len(data.client.list_collections()) == 2


@pytest.mark.parametrize('setting', ['parser_version', 'embedding_model', 'allow_partial', 'provider_available'])
def test_configuration_changes_after_prepare_do_not_start_paid_work(data, setting):
    svc, provider, embedder = service(data)
    accepted = prepare(svc)
    setattr(svc, setting, False if setting == 'provider_available' else True if setting == 'allow_partial' else 'changed')
    result = svc.run(accepted.job_id)
    assert result.status == S.FAILED
    assert result.reason_code == ('REPARSE_PROVIDER_UNAVAILABLE' if setting == 'provider_available'
                                  else 'REPARSE_CONFIGURATION_CHANGED')
    assert not provider.calls and not embedder.calls


def test_empty_page_limitation_and_partial_translation_are_both_preserved(data):
    pages = [ExtractedPage(1, 'New first.'), ExtractedPage(2, ''), ExtractedPage(3, 'New second.')]
    svc, _, _ = service(data, parser=lambda path: pages, provider=Provider(('New first',)), allow_partial=True)
    result = svc.run(prepare(svc).job_id)
    assert result.status == S.COMPLETED
    limitations = repo.get_job(data.db, result.job_id).limitations
    assert 'empty_pages:2' in limitations
    assert len([x for x in limitations if x.startswith('translation_failed:')]) == 1


def test_lookup_after_database_removal_does_not_create_a_database(data):
    svc, provider, embedder = service(data)
    data_path = Path(data.db)
    data_path.rename(data_path.with_suffix('.saved'))
    with pytest.raises(ReparseExecutionError, match='STATE_UNAVAILABLE'):
        lookup(svc)
    assert not data_path.exists() and not provider.calls and not embedder.calls


def test_own_client_close_failure_preserves_completed_result_and_reference(data, monkeypatch, capsys):
    from solo_leveling.infrastructure.storage.vector_store import ChromaCloseResult
    svc, _, _ = service(data)
    svc.client = None
    monkeypatch.setattr(module, 'get_client', lambda path: data.client)
    monkeypatch.setattr(module, 'close_client', lambda client: ChromaCloseResult.FAILED)
    result = svc.run(prepare(svc).job_id)
    assert result.status == S.COMPLETED
    assert svc._unreleased_clients == [data.client]
    assert 'CHROMA_CLOSE_FAILED' in capsys.readouterr().err
    assert lookup(svc) == result


def test_shared_client_is_never_closed_by_service(data, monkeypatch):
    def forbidden(client):
        pytest.fail('SHARED_CLIENT_CLOSED')
    monkeypatch.setattr(module, 'close_client', forbidden)
    svc, _, _ = service(data)
    assert svc.run(prepare(svc).job_id).status == S.COMPLETED


def test_unknown_state_after_execution_never_reexecutes_paid_stages(data, monkeypatch):
    svc, provider, embedder = service(data)
    original = svc._registration
    unavailable = False
    def read(key):
        if unavailable:
            raise sqlite3.OperationalError('SYNTHETIC_PRIVATE_DATABASE_PATH')
        return original(key)
    def failed_publish(*args, **kwargs):
        nonlocal unavailable
        unavailable = True
        raise OSError('SYNTHETIC_PRIVATE_UNKNOWN_RESULT')
    monkeypatch.setattr(svc, '_registration', read)
    monkeypatch.setattr(repo, 'publish_reparsed_index', failed_publish)
    accepted = prepare(svc)
    with pytest.raises(ReparseExecutionError, match='STATE_UNAVAILABLE'):
        svc.run(accepted.job_id)
    unavailable = False
    assert lookup(svc).status == S.IN_PROGRESS
    assert svc.run(accepted.job_id).status == S.IN_PROGRESS
    assert len(provider.calls) == 2 and len(embedder.calls) == 1
    assert count(data, 'processing_jobs') == 2


def test_new_chunk_word_boundaries_and_long_token_exception(data):
    text = ' '.join(f'word{n:04d}' for n in range(200)) + '.'
    long_token = 'https://synthetic.invalid/' + 'x' * 1400
    svc, _, _ = service(data, parser=lambda path: [ExtractedPage(1, text), ExtractedPage(2, long_token)])
    result = svc.run(prepare(svc).job_id)
    assert result.status == S.COMPLETED
    chunks = repo.get_chunks_by_parse_revision(data.db, result.new_parse_revision_id)
    words = [c for c in chunks if c.pdf_page == 1]
    assert len(words) > 1
    for chunk in words:
        start = text.index(chunk.original_text)
        end = start + len(chunk.original_text)
        assert start == 0 or text[start - 1].isspace()
        assert end == len(text) or text[end].isspace()
    tokens = [c for c in chunks if c.pdf_page == 2]
    assert len(tokens) > 1 and all(0 < len(c.original_text) <= 500 for c in tokens)


@pytest.mark.parametrize('phase', ['processing', 'completed', 'failed', 'interrupted'])
def test_read_only_status_mapping_matches_a_repository_contract(data, phase):
    svc, _, _ = service(data)
    accepted = prepare(svc)
    if phase == 'completed':
        svc.run(accepted.job_id)
    elif phase == 'failed':
        repo.fail_reparse(data.db, accepted.job_id, ['REPARSE_FAILED'])
    elif phase == 'interrupted':
        repo.mark_interrupted_jobs_on_startup(data.db)
    a = repo.get_reparse_request(data.db, 'request')
    b = lookup(svc)
    assert (b.status, b.job_id, b.new_parse_revision_id, b.new_embedding_set_id) == (
        a.disposition, a.job_id, a.new_parse_revision_id, a.new_embedding_set_id)


def test_retry_budget_change_after_admission_is_rejected_before_translation(data):
    svc, provider, embedder = service(data)
    accepted = prepare(svc)
    svc.translation.retry_policy = replace(svc.translation.retry_policy, max_attempts=1)
    result = svc.run(accepted.job_id)
    assert result.status == S.FAILED and result.reason_code == 'REPARSE_CONFIGURATION_CHANGED'
    assert not provider.calls and not embedder.calls


def test_admission_response_loss_does_not_adopt_or_duplicate_work(data, monkeypatch):
    original = repo.prepare_reparse
    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('SYNTHETIC_PRIVATE_ADMISSION_LOSS')
    svc, provider, embedder = service(data)
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'prepare_reparse', lost)
        with pytest.raises(ReparseExecutionError, match='STATE_UNAVAILABLE'):
            prepare(svc)
    result = prepare(svc)
    assert result.status == S.IN_PROGRESS
    assert count(data, 'processing_jobs') == 2
    with pytest.raises(ReparseExecutionError, match='NOT_OWNED'):
        svc.run(result.job_id)
    assert not provider.calls and not embedder.calls


def test_publication_transaction_rejects_interruption_after_last_checkpoint(data, monkeypatch):
    original = repo.publish_reparsed_index
    def interrupt(*args, **kwargs):
        repo.mark_interrupted_jobs_on_startup(data.db)
        original(*args, **kwargs)
    monkeypatch.setattr(repo, 'publish_reparsed_index', interrupt)
    svc, _, _ = service(data)
    result = svc.run(prepare(svc).job_id)
    assert result.status == S.INTERRUPTED
    assert repo.get_search_index(data.db, 'v1')['embedding_set_id'] == 'e1'
    assert count(data, 'search_indexes') == 1


def test_interruption_during_retry_wait_does_not_call_provider_again(data):
    class AlwaysUnavailable(Provider):
        def translate(self, request, settings):
            self.calls.append(request)
            raise TranslationProviderUnavailable(code=ProviderFailure.TIMEOUT)
    provider = AlwaysUnavailable()
    translation = TranslationService(provider, retry_policy=TranslationRetryPolicy(
        base_delay_seconds=0, jitter_seconds=0),
        sleep=lambda delay: repo.mark_interrupted_jobs_on_startup(data.db))
    svc, _, embedder = service(data, translation_service=translation)
    result = svc.run(prepare(svc).job_id)
    assert result.status == S.INTERRUPTED
    assert len(provider.calls) == 1 and not embedder.calls


def test_stale_base_is_rejected_without_admission_or_generation(data):
    svc, provider, embedder = service(data)
    result = svc.prepare('v1', 'p1', 'wrong-index', 'request')
    assert result.status == S.BASE_CHANGED and result.job_id is None
    assert count(data, 'processing_jobs') == 1
    assert not provider.calls and not embedder.calls


def test_new_section_catalog_and_chunk_links_do_not_replace_old_catalog(data):
    old = PaperSection('old-section', 'p1', 'Old heading', 1, 1, 1, 2, start_chunk_index=0, end_chunk_index=1)
    repo.insert_sections(data.db, [old])
    pages = [ExtractedPage(1, '1 Method\nNew first.', headings=[PageHeading('1 Method', 0)]),
             ExtractedPage(2, '2 Results\nNew second.', headings=[PageHeading('2 Results', 0)])]
    svc, _, _ = service(data, parser=lambda path: pages)
    result = svc.run(prepare(svc).job_id)
    assert result.status == S.COMPLETED
    assert repo.get_section_catalog(data.db, 'v1', 'p1') == [old]
    catalog = repo.get_section_catalog(data.db, 'v1', result.new_parse_revision_id)
    assert [section.title for section in catalog] == ['1 Method', '2 Results']
    assert all(section.parse_revision_id == result.new_parse_revision_id for section in catalog)
    chunks = repo.get_chunks_by_parse_revision(data.db, result.new_parse_revision_id)
    assert {c.section_id for c in chunks} == {section.section_id for section in catalog}


def test_lookup_status_and_reason_share_one_snapshot_during_concurrent_write(data, monkeypatch):
    # WAL에서는 읽는 도중에도 별도 연결이 다음 상태를 커밋할 수 있다.
    with closing(repo.get_connection(data.db)) as conn:
        assert conn.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    svc, _, _ = service(data)
    accepted = prepare(svc)
    read = svc._read_registration
    changed = False
    def read_then_change(conn, key):
        nonlocal changed
        result = read(conn, key)
        if not changed:
            changed = True
            repo.fail_reparse(data.db, accepted.job_id, ['REPARSE_FAILED'])
        return result
    monkeypatch.setattr(svc, '_read_registration', read_then_change)
    before = lookup(svc)
    assert before.status == S.IN_PROGRESS and before.reason_code is None
    after = lookup(svc)
    assert after.status == S.FAILED and after.reason_code == 'REPARSE_FAILED'


@pytest.mark.parametrize('link', ['embedding_translation', 'parse_version', 'job_status'])
def test_completed_lookup_rejects_broken_publication_chain(data, link):
    svc, _, _ = service(data)
    completed = svc.run(prepare(svc).job_id)
    with closing(repo.get_connection(data.db)) as conn, conn:
        if link == 'embedding_translation':
            conn.execute('UPDATE embedding_sets SET translation_revision_id=? WHERE embedding_set_id=?',
                         ('t1', completed.new_embedding_set_id))
        elif link == 'parse_version':
            repo_version = PaperVersion('other-version', 'paper', 'other-hash', 'other.pdf', 'other.pdf')
            conn.execute('INSERT INTO paper_versions VALUES (?,?,?,?,?,?)',
                         (repo_version.version_id, repo_version.paper_id, repo_version.file_hash,
                          repo_version.stored_path, repo_version.original_filename, repo_version.created_at))
            conn.execute('UPDATE parse_revisions SET version_id=? WHERE parse_revision_id=?',
                         ('other-version', completed.new_parse_revision_id))
        else:
            conn.execute('UPDATE processing_jobs SET status=? WHERE job_id=?', ('interrupted', completed.job_id))
    with pytest.raises(ReparseExecutionError, match='STATE_UNAVAILABLE'):
        lookup(svc)

@pytest.mark.parametrize('wrong_revision', ['p1', None])
def test_completed_result_must_match_request_revision(data, wrong_revision):
    svc, _, _ = service(data)
    completed = svc.run(prepare(svc).job_id)
    assert completed.result_available and lookup(svc) == completed
    with closing(repo.get_connection(data.db)) as conn, conn:
        conn.execute('UPDATE reparse_requests SET new_parse_revision_id=? WHERE request_key=?',
                     (wrong_revision, 'request'))
    for replay in (lookup, prepare):
        with pytest.raises(ReparseExecutionError, match='REPARSE_STATE_UNAVAILABLE'):
            replay(svc)
