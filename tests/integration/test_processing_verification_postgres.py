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
