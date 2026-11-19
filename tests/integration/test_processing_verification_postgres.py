"""검증 claim은 대상 하나만 바꾸며 전역 만료 정리·재시도를 수행하지 않는다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, make_paper, new_id  # noqa: F401
from solo_leveling.interfaces.http.processing_jobs import PostgresProcessingJobStore
from solo_leveling.workers.verification import VerificationConfig, VerificationJobStore, IsolatedPool

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def setup(pool, db):
    owner = new_id()
    paper = make_paper(db, owner)
    job = PostgresProcessingJobStore(pool).enqueue(owner, paper, 'v1', 'full_processing', new_id(), 'a' * 64)
    row = db('SELECT current_database() AS db, current_schema() AS schema')[0]
    config = VerificationConfig(row['db'], row['schema'], owner, job.job_id, paper, 'v1', 'a' * 64,
                                'verification-worker', 60, 100_000)
    return config, VerificationJobStore(IsolatedPool(pool, config), config)


def claim(store): return store.claim('verification-worker', lease_seconds=60)


def test_exact_target_claim_does_not_change_other_queued_or_expired_jobs(pool, db):
    other, _ = setup(pool, db)
    expired, exp_store = setup(pool, db)
    claim(exp_store)
    db("UPDATE server_processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (expired.job_id,))
    before = db('SELECT * FROM server_processing_jobs WHERE job_id IN (%s,%s) ORDER BY job_id', (other.job_id, expired.job_id))
    target, store = setup(pool, db)
    result = claim(store)
    assert result.job_id == target.job_id and result.attempt == 1 and result.retryable is False
    assert claim(store) is None
    assert db('SELECT * FROM server_processing_jobs WHERE job_id IN (%s,%s) ORDER BY job_id', (other.job_id, expired.job_id)) == before


@pytest.mark.parametrize('change', ['owner_user_id', 'job_id', 'paper_id', 'version_id', 'payload_hash'])
def test_wrong_identity_cannot_claim(pool, db, change):
    config, _ = setup(pool, db)
    altered = replace(config, **{change: 'f' * 64 if change == 'payload_hash' else new_id()})
    assert claim(VerificationJobStore(IsolatedPool(pool, altered), altered)) is None
    assert db('SELECT attempt FROM server_processing_jobs WHERE job_id=%s', (config.job_id,))[0]['attempt'] == 0


def test_concurrent_claim_has_one_winner_and_expiry_does_not_restart(pool, db):
    config, store = setup(pool, db)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: claim(store), range(2)))
    assert sum(r is not None for r in results) == 1
    db("UPDATE server_processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (config.job_id,))
    assert claim(store) is None
    assert db('SELECT status,attempt FROM server_processing_jobs WHERE job_id=%s', (config.job_id,))[0] == dict(status='running', attempt=1)


def test_wrong_database_rejected_before_mutation(pool, db):
    config, _ = setup(pool, db)
    config = replace(config, database_name='wrong_database')
    with pytest.raises(RuntimeError, match='database_mismatch'):
        claim(VerificationJobStore(IsolatedPool(pool, config), config))
    assert db('SELECT attempt FROM server_processing_jobs WHERE job_id=%s', (config.job_id,))[0]['attempt'] == 0


def test_single_runner_reaches_real_staging_and_publication_without_external_calls(pool, db, monkeypatch):
    from types import SimpleNamespace as NS
    from tests.integration.test_remote_processing_intake_postgres import admitted_source
    from solo_leveling.interfaces.http.remote_processing_intake import PostgresRemoteProcessingIntake
    from solo_leveling.interfaces.http.remote_upload_consents import NOTICE_VERSION
    from solo_leveling.interfaces.http.object_storage import DownloadedObject
    from solo_leveling.workers.verification import run_once
    from tests.fixtures.processing_result import result, LIMITS, SETTINGS, SPEC
    import solo_leveling.workers.processing_composition as composition
    owner, intent, storage, ai = admitted_source(pool, db)
    job = PostgresRemoteProcessingIntake(pool).admit(owner, intent, new_id(), storage, ai, NOTICE_VERSION)
    row = db('SELECT current_database() AS db, current_schema() AS schema')[0]
    target = db('SELECT * FROM server_processing_jobs WHERE job_id=%s', (job.job_id,))[0]
    config = VerificationConfig(row['db'], row['schema'], owner, job.job_id, str(target['paper_id']),
        target['server_version_id'], target['payload_hash'], 'verification-worker', 60, 100_000)
    monkeypatch.setattr(composition, 'ProcessingStagesFactory', lambda *_args, **_kwargs:
        lambda source, check: NS(run=lambda current, body: result(current)))
    class Objects:
        calls = 0
        def read_object(self, key, *, expected_size):
            self.calls += 1
            return DownloadedObject(key, expected_size, 'application/pdf', 'a' * 64, True, b'%PDF-test!')
    objects = Objects()
    resources = NS(pool=pool, objects=objects, stage_options=dict(limits=LIMITS,
        service_config=NS(translation_settings=SETTINGS), embedding_spec=SPEC))
    assert run_once(config, resources) == 'published'
    assert run_once(config, resources) == 'not_claimed'
    assert objects.calls == 1
    assert db('SELECT status FROM server_processing_jobs WHERE job_id=%s', (job.job_id,))[0]['status'] == 'succeeded'
    assert db('SELECT count(*) AS n FROM server_processing_publications WHERE source_job_id=%s', (job.job_id,))[0]['n'] == 1


def test_partial_translation_recovery_reuses_success_and_publishes(pool, db, monkeypatch):
    from types import SimpleNamespace as NS

    from solo_leveling.application.translation.service import TranslationService
    from solo_leveling.domain.models import Chunk
    from solo_leveling.domain.remote_processing import EmbeddingBatch, ProcessedTranslation
    from solo_leveling.interfaces.http.object_storage import DownloadedObject
    from solo_leveling.interfaces.http.remote_processing_intake import PostgresRemoteProcessingIntake
    from solo_leveling.interfaces.http.remote_upload_consents import NOTICE_VERSION
    import solo_leveling.workers.processing_composition as composition
    from solo_leveling.workers.verification import run_once
    from tests.fixtures.processing_result import LIMITS, SETTINGS, SPEC, result
    from tests.integration.test_remote_processing_intake_postgres import admitted_source

    owner, intent, storage, ai = admitted_source(pool, db)
    job = PostgresRemoteProcessingIntake(pool).admit(owner, intent, new_id(), storage, ai, NOTICE_VERSION)
    identity = db('SELECT current_database() AS db, current_schema() AS schema')[0]
    target = db('SELECT * FROM server_processing_jobs WHERE job_id=%s', (job.job_id,))[0]
    config = VerificationConfig(identity['db'], identity['schema'], owner, job.job_id,
        str(target['paper_id']), target['server_version_id'], target['payload_hash'],
        'verification-worker', 60, 100_000)
    calls = []

    class Objects:
        def read_object(self, key, *, expected_size):
            return DownloadedObject(key, expected_size, 'application/pdf', 'a' * 64, True, b'%PDF-test!')

    def stages(_authorization, *, reuse_translations=None, **_options):
        def bind(source, _check):
            def process(current, _body):
                base = result(current)
                first = replace(base.document.chunks[0], section_id=None,
                                original_text='The first paragraph describes the proposed attention mechanism.')
                second = replace(first, chunk_id=new_id(), chunk_index=1,
                                 original_text='The second paragraph describes the evaluation methodology.')
                chunks = (first, second)
                reusable = reuse_translations(current, chunks) if reuse_translations else None

                class Provider:
                    def translate(self, request, settings):
                        index = next(chunk.chunk_index for chunk in chunks if chunk.chunk_id == request.chunk_id)
                        calls.append(index)
                        if index == 1 and reusable is None:
                            return ''
                        return '첫 번째 번역을 보존합니다.' if index == 0 else '두 번째 번역을 복구했습니다.'

                batch = TranslationService(Provider()).translate(
                    tuple(Chunk(c.chunk_id, c.parse_revision_id, c.chunk_index, c.original_text,
                                pdf_page=c.pdf_page) for c in chunks),
                    SETTINGS, reusable=reusable,
                )
                translations = tuple(ProcessedTranslation(item.request.chunk_id, item.text,
                    item.failure_code, item.status) for item in batch.items)
                embeddings = None if any(not item.indexable for item in batch.items) else EmbeddingBatch(
                    SPEC, tuple(c.chunk_id for c in chunks), ((1.0, 0.0), (0.0, 1.0)))
                return replace(base, document=replace(base.document, chunks=chunks, sections=()),
                               translations=translations, embeddings=embeddings,
                               status='prepared' if embeddings else 'translation_incomplete')

            return NS(run=process)

        return bind

    monkeypatch.setattr(composition, 'ProcessingStagesFactory', stages)
    resources = NS(pool=pool, objects=Objects(), stage_options=dict(limits=LIMITS,
        service_config=NS(translation_settings=SETTINGS), embedding_spec=SPEC))
    assert run_once(config, resources) == 'translation_checkpoint_saved'
    assert calls == [0, 1]
    saved = db('SELECT payload FROM server_processing_result_staging WHERE job_id=%s AND payload_version=4',
               (job.job_id,))[0]['payload']['chunks']
    assert saved[0]['text'] == '첫 번째 번역을 보존합니다.'
    assert saved[1]['text'] is None and saved[1]['failure_code'] == 'empty_translation'
    assert db('SELECT status,attempt FROM server_processing_jobs WHERE job_id=%s', (job.job_id,)) == [
        dict(status='failed', attempt=1)]
    assert run_once(config, resources) == 'not_claimed'

    recovery = replace(config, recover_translation=True)
    assert run_once(recovery, resources) == 'published'
    assert calls == [0, 1, 1]
    assert run_once(recovery, resources) == 'not_claimed'
    assert db('SELECT status,attempt FROM server_processing_jobs WHERE job_id=%s', (job.job_id,)) == [
        dict(status='succeeded', attempt=2)]
    assert db('SELECT count(*) AS n FROM server_processing_publications WHERE source_job_id=%s',
              (job.job_id,))[0]['n'] == 1
