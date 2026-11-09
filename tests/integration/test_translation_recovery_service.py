"""임시 SQLite/Chroma와 합성 번역·임베딩만으로 #222 실행 경로를 검증한다."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from solo_leveling.application.translation.service import TranslationService
from solo_leveling.application.evidence_qa.errors import InvalidArgumentError, ResourceNotFoundError
from solo_leveling.domain.models import Chunk, Paper, PaperVersion, ParseRevision, ProcessingJob, JobStatus, JobStage
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.domain.translation_recovery import RecoveryConfigurationError
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import init_db
from solo_leveling.workers import translation_recovery as recovery
from tests.unit.test_translation_recovery import batch, emb, ok, fail


SETTINGS = TranslationSettings('p', 'm', 'v1')


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / 'recovery.sqlite3')
    init_db(path)
    repo.insert_paper(path, Paper('paper'))
    repo.insert_version(path, PaperVersion('v1', 'paper', 'hash', 'synthetic.pdf', 'synthetic.pdf'))
    repo.insert_parse_revision(path, ParseRevision('p1', 'v1', 3))
    repo.insert_chunks(path, [Chunk(f'c{i}', 'p1', i, f'original {i}') for i in range(98)])
    repo.insert_job(path, ProcessingJob('j1', 'v1', JobStatus.PROCESSING, JobStage.TRANSLATE))
    repo.save_translation_batch(path, batch('t1', [fail(i) if i in (3, 7) else ok(i) for i in range(98)]))
    repo.publish_search_index(path, 'v1', 'p1', emb('e1', 't1'), 'j1',
                              [f'c{i}' for i in range(98) if i not in (3, 7)], ['partial'])
    return path


class Provider:
    def __init__(self):
        self.calls = []
        self.failures = set()
        self.hook = lambda: None

    def translate(self, request, settings):
        assert settings == SETTINGS
        self.calls.append(request.chunk_id)
        self.hook()
        return '' if request.chunk_id in self.failures else f'복구 {request.chunk_id}'


@pytest.fixture
def setup(db, tmp_path, chroma_client_factory):
    client = chroma_client_factory(str(tmp_path / 'chroma'))
    provider, embeddings = Provider(), []

    def embedder(texts, *, model_name):
        assert model_name == 'model'
        embeddings.append(list(texts))
        return [[1., 0., 0., 0.] for _ in texts]

    service = recovery.TranslationRecoveryService(db, str(tmp_path / 'unused'),
        translation_service=TranslationService(provider), translation_settings=SETTINGS,
        embedder=embedder, chroma_client=client)
    return service, provider, embeddings, client


def execute(service, key='request', base='e1'):
    accepted = service.prepare('v1', base, key)
    assert accepted.status == 'ACCEPTED'
    return service.run(accepted.job_id)


def test_partial_then_full_recovery_preserves_all_previous_contexts(db, setup):
    service, provider, embeddings, client = setup
    old = repo.get_or_create_default_context(db, 'v1')
    before = repo.get_translated_chunks(db, 'p1', 't1')
    provider.failures = {'c7'}
    partial = execute(service)
    assert (partial.status, partial.outcome, partial.target_count,
            partial.recovered_count, partial.remaining_failed_count) == ('COMPLETED', 'PARTIAL', 2, 1, 1)
    assert provider.calls == ['c3', 'c7']
    assert len(embeddings[0]) == 97
    middle = repo.get_or_create_default_context(db, 'v1')
    provider.failures.clear()
    full = execute(service, 'second', partial.new_embedding_set_id)
    assert (full.status, full.outcome, full.target_count, full.recovered_count,
            full.remaining_failed_count) == ('COMPLETED', 'FULL', 1, 1, 0)
    assert provider.calls == ['c3', 'c7', 'c7']
    assert len(embeddings[1]) == 98
    latest = repo.get_or_create_default_context(db, 'v1')
    assert len({old.context_id, middle.context_id, latest.context_id}) == 3
    for context, count in ((old, 96), (middle, 97), (latest, 98)):
        saved = repo.get_learning_context(db, context.context_id)
        assert saved.embedding_set_id == context.embedding_set_id
        assert len(repo.get_translated_chunks(db, saved.parse_revision_id, saved.translation_revision_id)) == count
    assert repo.get_translated_chunks(db, 'p1', 't1') == before
    assert repo.get_chunk(db, 'c3').text is None
    assert service.lookup('v1', 'e1', 'request') == partial
    assert service.prepare('v1', 'e1', 'request') == partial
    assert len(provider.calls) == 3
    # 성공한 뒤 같은 실행권을 다시 소비해도 번역하지 않는다.
    assert service.run(full.job_id) == full
    assert len(provider.calls) == 3


def test_zero_success_does_not_embed_publish_or_retry(db, setup):
    service, provider, embeddings, client = setup
    provider.failures = {'c3', 'c7'}
    result = execute(service)
    assert (result.status, result.outcome, result.recovered_count,
            result.remaining_failed_count) == ('FAILED', 'NO_PROGRESS', 0, 2)
    assert not embeddings and not client.list_collections()
    assert repo.get_search_index(db, 'v1')['embedding_set_id'] == 'e1'
    assert service.prepare('v1', 'e1', 'request') == result
    assert provider.calls == ['c3', 'c7']


def test_lookup_missing_conflict_and_reads_do_not_start_work(setup):
    service, provider, embeddings, client = setup
    with pytest.raises(ResourceNotFoundError):
        service.lookup('v1', 'e1', 'absent')
    accepted = service.prepare('v1', 'e1', 'key')
    assert service.lookup('v1', 'e1', 'key').status == 'IN_PROGRESS'
    conflict = service.prepare('v1', 'other', 'key')
    assert conflict.status == 'REQUEST_CONFLICT' and conflict.job_id is None
    assert service.prepare('v1', 'e1', 'different').status == 'IN_PROGRESS'
    assert not provider.calls and not embeddings and not client.list_collections()
    assert service.cleanup(accepted.job_id) == 'INDEX_CLEANUP_NOT_ALLOWED'


def test_concurrent_prepare_and_run_have_one_execution(db, setup):
    service, provider, embeddings, _ = setup
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: service.prepare('v1', 'e1', 'key'), range(4)))
    assert sum(r.status == 'ACCEPTED' for r in results) == 1
    job_id = next(r.job_id for r in results if r.status == 'ACCEPTED')
    entered, release = Event(), Event()
    def wait():
        entered.set()
        assert release.wait(5)
    provider.hook = wait
    with ThreadPoolExecutor(2) as pool:
        running = pool.submit(service.run, job_id)
        try:
            assert entered.wait(5)
            assert service.run(job_id).status == 'IN_PROGRESS'
        finally:
            release.set()
        assert running.result(20).status == 'COMPLETED'
    assert provider.calls == ['c3', 'c7'] and len(embeddings) == 1


def test_no_write_transaction_during_provider_and_embedding(db, setup):
    service, provider, _, _ = setup
    def acquire_write():
        with closing(repo.get_connection(db)) as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.rollback()
    provider.hook = acquire_write
    original = service.embedder
    def embed(*args, **kwargs):
        acquire_write()
        return original(*args, **kwargs)
    service.embedder = embed
    assert execute(service).status == 'COMPLETED'


@pytest.mark.parametrize('fault', ['dimension', 'nan', 'bool', 'empty'])
def test_invalid_vectors_preserve_old_default_without_writes(db, setup, fault):
    service, _, _, client = setup
    service.embedder = lambda texts, **kw: {
        'dimension': [[1.]] * len(texts), 'nan': [[float('nan')] * 4] * len(texts),
        'bool': [[True] * 4] * len(texts), 'empty': [],
    }[fault]
    result = execute(service)
    assert result.status == 'FAILED' and result.reason_code == 'RECOVERY_EMBEDDING_FAILED'
    assert not client.list_collections()
    assert repo.get_search_index(db, 'v1')['embedding_set_id'] == 'e1'


def test_verification_failure_and_explicit_owned_cleanup(db, setup, monkeypatch, capsys):
    service, provider, _, client = setup
    def reject(*args, **kwargs):
        raise RuntimeError('SYNTHETIC_PRIVATE_BODY')
    monkeypatch.setattr(recovery, 'verify_chunk_embeddings', reject)
    result = execute(service, key='SYNTHETIC_USER_REQUEST_KEY')
    assert result.status == 'FAILED' and result.reason_code == 'RECOVERY_INDEX_FAILED'
    candidate = repo.get_unpublished_recovery_embedding_set(db, result.job_id)
    assert candidate and client.get_collection(f'chunks-{candidate}').count() == 98
    client.create_collection('unrelated')
    assert service.cleanup(result.job_id) == 'INDEX_CLEANUP_COMPLETED'
    assert [c.name for c in client.list_collections()] == ['unrelated']
    assert repo.get_search_index(db, 'v1')['embedding_set_id'] == 'e1'
    captured = capsys.readouterr()
    assert captured.out == '' and 'SYNTHETIC_PRIVATE_BODY' not in captured.err
    assert 'SYNTHETIC_USER_REQUEST_KEY' not in captured.err
    assert 'original 3' not in captured.err and '복구 c3' not in captured.err


def test_publish_commit_then_response_loss_replays_without_cleanup(db, setup, monkeypatch):
    service, provider, embeddings, client = setup
    original = repo.publish_recovered_index
    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('SYNTHETIC_RESPONSE_LOST')
    monkeypatch.setattr(repo, 'publish_recovered_index', lost)
    result = execute(service)
    assert result.status == 'COMPLETED' and result.outcome == 'FULL'
    assert service.lookup('v1', 'e1', 'request') == result
    assert service.cleanup(result.job_id) == 'INDEX_CLEANUP_NOT_ALLOWED'
    assert client.get_collection(f'chunks-{result.new_embedding_set_id}').count() == 98
    assert service.prepare('v1', 'e1', 'request') == result
    assert len(provider.calls) == 2 and len(embeddings) == 1


def test_restart_interrupted_is_replayed_not_executed(db, setup):
    service, provider, embeddings, _ = setup
    accepted = service.prepare('v1', 'e1', 'request')
    assert repo.mark_interrupted_jobs_on_startup(db) == 1
    assert service.run(accepted.job_id).status == 'INTERRUPTED'
    assert service.prepare('v1', 'e1', 'request').status == 'INTERRUPTED'
    assert not provider.calls and not embeddings


def test_changed_default_rejected_at_publish_without_overwrite(db, setup, monkeypatch):
    service, _, _, client = setup
    original = recovery.verify_chunk_embeddings
    def swap(*args, **kwargs):
        original(*args, **kwargs)
        with closing(repo.get_connection(db)) as conn, conn:
            conn.execute("INSERT INTO embedding_sets SELECT 'other', translation_revision_id, model_name, dimension, created_at FROM embedding_sets WHERE embedding_set_id='e1'")
            conn.execute("UPDATE search_indexes SET is_default=0 WHERE embedding_set_id='e1'")
            conn.execute("INSERT INTO search_indexes SELECT 'other', version_id, parse_revision_id, translation_revision_id, job_id, chunk_count, 1 FROM search_indexes WHERE embedding_set_id='e1'")
    monkeypatch.setattr(recovery, 'verify_chunk_embeddings', swap)
    result = execute(service)
    assert result.status == 'FAILED' and result.reason_code == 'RECOVERY_BASE_CHANGED'
    assert repo.get_search_index(db, 'v1')['embedding_set_id'] == 'other'


def test_configuration_and_input_rejected_before_acceptance(db, setup):
    service, provider, embeddings, _ = setup
    service.translation_settings = TranslationSettings('different', 'model', 'v1')
    with pytest.raises(RecoveryConfigurationError):
        service.prepare('v1', 'e1', 'request')
    with closing(repo.get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM translation_recovery_requests').fetchone()[0] == 0
    for key in ('', ' ', 'x' * 201, 'x\nkey', None):
        with pytest.raises(InvalidArgumentError):
            service.prepare('v1', 'e1', key)
    assert not provider.calls and not embeddings


def test_db_uncertain_after_publish_failure_does_not_delete_or_mark_failed(db, setup, monkeypatch):
    service, _, _, client = setup
    def uncertain(*args, **kwargs):
        monkeypatch.setattr(repo, 'get_search_index_for_job', lambda *a: (_ for _ in ()).throw(OSError()))
        raise RuntimeError('SYNTHETIC_COMMIT_UNKNOWN')
    monkeypatch.setattr(repo, 'publish_recovered_index', uncertain)
    result = execute(service)
    assert result.status == 'UNKNOWN' and result.reason_code == 'RECOVERY_STATE_UNAVAILABLE'
    assert repo.get_job(db, result.job_id).status == JobStatus.PROCESSING
    assert len(client.list_collections()) == 1
    assert service.cleanup(result.job_id) == 'INDEX_CLEANUP_NOT_ALLOWED'


def test_cleanup_read_failure_or_delete_failure_is_safe(db, setup, monkeypatch, capsys):
    service, _, _, client = setup
    def reject(*args, **kwargs):
        raise RuntimeError('SYNTHETIC_PRIVATE_KEY')
    monkeypatch.setattr(recovery, 'verify_chunk_embeddings', reject)
    result = execute(service)
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'get_unpublished_recovery_embedding_set', reject)
        assert service.cleanup(result.job_id) == 'INDEX_CLEANUP_FAILED'
    assert len(client.list_collections()) == 1
    monkeypatch.setattr(client, 'delete_collection', reject)
    assert service.cleanup(result.job_id) == 'INDEX_CLEANUP_FAILED'
    assert 'SYNTHETIC_PRIVATE_KEY' not in capsys.readouterr().err
    assert repo.get_job(db, result.job_id).status == JobStatus.FAILED


def test_late_worker_after_interrupt_cannot_save_or_publish(db, setup):
    service, provider, embeddings, client = setup
    provider.hook = lambda: repo.mark_interrupted_jobs_on_startup(db)
    result = execute(service)
    assert result.status == 'INTERRUPTED'
    assert not embeddings and not client.list_collections()
    assert repo.get_search_index(db, 'v1')['embedding_set_id'] == 'e1'


def test_run_in_other_service_does_not_steal_work_and_cancel_is_replayed(db, setup):
    service, provider, embeddings, client = setup
    accepted = service.prepare('v1', 'e1', 'key')
    other = recovery.TranslationRecoveryService(db, 'unused', translation_service=service.translation_service,
        translation_settings=SETTINGS, embedder=service.embedder, chroma_client=client)
    assert other.run(accepted.job_id).status == 'IN_PROGRESS'
    assert service.cancel_before_start(accepted.job_id)
    assert service.run(accepted.job_id).status == 'FAILED'
    assert not service.cancel_before_start(accepted.job_id)
    assert not provider.calls and not embeddings


def test_no_target_after_full_recovery_does_not_accept_new_work(db, setup):
    service, provider, embeddings, _ = setup
    result = execute(service)
    calls = list(provider.calls)
    assert service.prepare('v1', result.new_embedding_set_id, 'new').status == 'NO_TARGET'
    assert provider.calls == calls and len(embeddings) == 1


def test_replay_does_not_require_current_provider_configuration(setup):
    service, provider, embeddings, _ = setup
    result = execute(service)
    service.translation_settings = TranslationSettings('removed', 'removed', 'removed')
    assert service.prepare('v1', 'e1', 'request') == result
    assert len(provider.calls) == 2 and len(embeddings) == 1


def test_cleanup_requires_actual_collection_removal(db, setup, monkeypatch):
    service, _, _, client = setup
    monkeypatch.setattr(recovery, 'verify_chunk_embeddings',
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError()))
    result = execute(service)
    monkeypatch.setattr(client, 'delete_collection', lambda **kw: None)
    assert service.cleanup(result.job_id) == 'INDEX_CLEANUP_FAILED'
    assert len(client.list_collections()) == 1


def test_preview_resolves_current_base_without_accepting_and_disabled_provider_does_not_run(db, setup):
    service, provider, embeddings, _ = setup
    preview = service.preview('v1', request_key='preview-key')
    assert preview == {'status': 'AVAILABLE', 'target_count': 2, 'translated_count': 96,
                       'base_embedding_set_id': 'e1'}
    service.provider_available = False
    assert service.preview('v1')['status'] == 'UNAVAILABLE'
    with pytest.raises(RecoveryConfigurationError):
        service.prepare('v1', 'e1', 'key')
    with closing(repo.get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM translation_recovery_requests').fetchone()[0] == 0
    assert not provider.calls and not embeddings


def test_cleanup_cannot_race_interrupted_worker_still_writing(db, setup, monkeypatch):
    service, _, _, client = setup
    entered, release = Event(), Event()
    original = recovery.upsert_chunk_embeddings
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(8)
        return original(*args, **kwargs)
    monkeypatch.setattr(recovery, 'upsert_chunk_embeddings', delayed)
    accepted = service.prepare('v1', 'e1', 'key')
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(service.run, accepted.job_id)
        try:
            assert entered.wait(5)
            repo.mark_interrupted_jobs_on_startup(db)
            assert service.cleanup(accepted.job_id) == 'INDEX_CLEANUP_NOT_ALLOWED'
        finally:
            release.set()
            result = running.result(20)
    assert result.status == 'INTERRUPTED'
    assert repo.get_search_index(db, 'v1')['embedding_set_id'] == 'e1'
    assert service.cleanup(accepted.job_id) == 'INDEX_CLEANUP_COMPLETED'
    assert not client.list_collections()


def test_new_service_does_not_assume_previous_writer_has_stopped(db, setup, monkeypatch):
    service, _, _, client = setup
    monkeypatch.setattr(recovery, 'verify_chunk_embeddings',
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError()))
    result = execute(service)
    other = recovery.TranslationRecoveryService(db, 'unused', translation_service=service.translation_service,
        translation_settings=SETTINGS, embedder=service.embedder, chroma_client=client)
    assert other.cleanup(result.job_id) == 'INDEX_CLEANUP_NOT_ALLOWED'
    assert len(client.list_collections()) == 1
    assert service.cleanup(result.job_id) == 'INDEX_CLEANUP_COMPLETED'


def test_recovery_replaces_translation_failures_but_preserves_extraction_limits(db, setup):
    service, provider, _, _ = setup
    original = ['empty_pages:2', 'translation_failed:c3:provider_unavailable']
    repo.update_job_status(db, 'j1', JobStatus.READY, limitations=original)
    provider.failures = {'c7'}
    partial = execute(service)
    assert repo.get_job(db, partial.job_id).limitations == [
        'empty_pages:2', 'translation_failed:c7:empty_translation']
    provider.failures.clear()
    full = execute(service, 'second', partial.new_embedding_set_id)
    assert repo.get_job(db, full.job_id).limitations == ['empty_pages:2']
    assert repo.get_job(db, 'j1').limitations == original


def test_original_context_search_quotes_and_summary_remain_fixed_after_service_recovery(db, setup):
    from solo_leveling.application.evidence_qa.search import ContextSearchService
    from solo_leveling.application.paper_detail.service import PaperDetailService
    from solo_leveling.domain.models import Evidence
    from solo_leveling.domain.paper_summary import SummaryItem
    from solo_leveling.infrastructure.retrieval.paper_detail import SQLitePaperDetailReader
    from solo_leveling.infrastructure.retrieval.sqlite_chroma import SQLiteChromaRetriever, SQLiteContextReader
    from solo_leveling.infrastructure.storage.vector_store import upsert_chunk_embeddings

    service, _, _, client = setup
    chunks = repo.get_translated_chunks(db, 'p1', 't1')
    upsert_chunk_embeddings(client, [c.chunk_id for c in chunks], [[1.,0.,0.,0.]] * len(chunks),
                            'e1', 'paper', 'v1', [c.text for c in chunks])
    old = repo.get_or_create_default_context(db, 'v1')
    repo.save_evidences(db, old.context_id, [Evidence('old', 'c0', '번역 0', 'original 0')])
    repo.save_summary(db, old.context_id, 'paper', [SummaryItem('claim', '기존 주장', ('old',))])
    before = PaperDetailService(SQLitePaperDetailReader(db)).get(old.context_id)
    assert execute(service).status == 'COMPLETED'
    # 복구 서비스 자체는 새 context를 만들지 않는다.
    with closing(repo.get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM learning_contexts').fetchone()[0] == 1
    assert PaperDetailService(SQLitePaperDetailReader(db)).get(old.context_id) == before
    assert repo.get_evidences(db, old.context_id, ['old']).evidence[0].quote_ko == '번역 0'
    new = repo.get_or_create_default_context(db, 'v1')
    search = ContextSearchService(SQLiteContextReader(db), SQLiteChromaRetriever(
        db, client, embedder=lambda *a, **kw: [[1.,0.,0.,0.]]))
    assert len(search.search('합성 질문', old.context_id, 98).items) == 96
    assert len(search.search('합성 질문', new.context_id, 98).items) == 98
    with pytest.raises(ValueError):
        repo.save_evidences(db, old.context_id, [Evidence('wrong', 'c3', '복구 c3', 'original 3')])


def test_owned_client_close_failure_does_not_authorize_cleanup(db, setup, monkeypatch):
    service, _, _, client = setup
    service.client = None
    monkeypatch.setattr(recovery, 'get_client', lambda path: client)
    monkeypatch.setattr(recovery, 'verify_chunk_embeddings',
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(recovery, 'close_client', lambda value: recovery.ChromaCloseResult.FAILED)
    result = execute(service)
    assert result.status == 'FAILED'
    assert service.cleanup(result.job_id) == 'INDEX_CLEANUP_NOT_ALLOWED'
    assert len(client.list_collections()) == 1
