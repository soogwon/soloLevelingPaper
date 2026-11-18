"""합성 작업의 소유권·상태·페이지·조회 무부작용을 실제 PostgreSQL에서 검증한다."""
# ruff: noqa: F811
import os
from uuid import uuid4
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores, make_paper, HASH  # noqa: F401
from tests.fixtures.pgvector_retrieval_contract import PgvectorContractStore
from solo_leveling.interfaces.http.remote_job_queries import RemoteJobQueries

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='로컬 PostgreSQL 필요')


def test_owner_pagination_and_reads_never_claim_or_retry(db, stores, pool):
    owner = 'jobs-' + uuid4().hex
    paper = make_paper(db, owner)
    jobs = [stores[0].enqueue(owner, paper, 'v1', 'full_processing', str(uuid4()), HASH) for _ in range(23)]
    query = RemoteJobQueries(pool, lambda: owner)
    first = query.list(limit=20)
    assert len(first['jobs']) == 20 and first['next_after']
    last = query.list(first['next_after'], 20)
    assert last['next_after'] is None and len(last['jobs']) == 3
    ids = [j['job_id'] for j in first['jobs'] + last['jobs']]
    assert len(set(ids)) == 23 and set(ids) == {j.job_id for j in jobs}
    for _ in range(2):
        assert query.get(jobs[0].job_id)['result']['status'] == 'queued'
    assert query.get(str(uuid4())) == {'status': 'not_found'}
    other = RemoteJobQueries(pool, lambda: 'other')
    assert other.get(jobs[0].job_id) == {'status': 'not_found'}
    assert other.list()['jobs'] == []
    assert db('SELECT DISTINCT status,attempt FROM server_processing_jobs WHERE owner_user_id=%s', (owner,)) == [dict(status='queued', attempt=0)]
    for job in jobs:
        stores[0].cancel(owner, job.job_id)
    db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE paper_id=%s', (paper,))
    assert query.get(jobs[0].job_id) == {'status': 'not_found'}
    assert query.list()['jobs'] == []


def test_error_projection_drops_secrets_and_unknown_provider_codes(db, stores, pool):
    owner = 'errors-' + uuid4().hex
    paper = make_paper(db, owner)
    job = stores[0].enqueue(owner, paper, 'v1', 'parse', str(uuid4()), HASH)
    db("UPDATE server_processing_jobs SET status='failed',error_code='private_provider_detail',finished_at=clock_timestamp() WHERE job_id=%s", (job.job_id,))
    result = RemoteJobQueries(pool, lambda: owner).get(job.job_id)['result']
    assert result['error_code'] == 'processing_failed' and result['status'] == 'failed'
    assert 'private_provider_detail' not in str(result)
    assert not {'owner_user_id','lease_token','payload_hash','request_key','lease_owner'} & result.keys()
    assert result['publication_id'] is None and result['web_path'] is None


def test_old_job_keeps_its_own_publication_after_new_publish(db, stores, pool):
    remote = PgvectorContractStore(db, stores, pool)
    first = remote.publish()
    job = db('SELECT source_job_id FROM server_processing_publications WHERE publication_id=%s', (first.publication,))[0]['source_job_id']
    query = RemoteJobQueries(pool, lambda: remote.owner)
    before = query.get(str(job))
    assert before['result']['publication_id'] == first.publication
    assert before['result']['status'] == 'completed'
    remote.publish(100, base=first)
    assert query.get(str(job)) == before
