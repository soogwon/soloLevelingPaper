"""요청 재전송·실행 세대·게시와 취소 경합을 합성 PostgreSQL로 검증한다."""
# ruff: noqa: F811
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores, make_paper, HASH  # noqa: F401
from tests.fixtures.pgvector_retrieval_contract import PgvectorContractStore
from solo_leveling.interfaces.http.processing_cancellations import PostgresProcessingCancellationStore
from solo_leveling.interfaces.http.processing_jobs import ProcessingJobConflict, ProcessingJobNotFound, ProcessingJobLeaseLost

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='로컬 PostgreSQL 필요')


def setup_job(db, stores):
    owner = 'cancel-' + uuid4().hex
    paper = make_paper(db, owner)
    job = stores[0].enqueue(owner, paper, 'v1', 'full_processing', str(uuid4()), HASH)
    return owner, paper, job


def test_concurrent_replay_reconnect_and_key_conflict(db, stores, pool):
    owner, paper, job = setup_job(db, stores)
    key = str(uuid4())
    def cancel():
        return PostgresProcessingCancellationStore(pool).cancel(owner,job.job_id,key,1)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: cancel(), range(2)))
    assert results[0] == results[1] == cancel()
    assert results[0]['revision'] == results[0]['applied_revision'] == 2
    assert db('SELECT count(*) AS n FROM server_processing_cancel_requests WHERE owner_user_id=%s',(owner,)) == [{'n':1}]
    with pytest.raises(ProcessingJobConflict, match='request_key_conflict'):
        PostgresProcessingCancellationStore(pool).cancel(owner,job.job_id,key,2)
    other = stores[0].enqueue(owner,paper,'v1','parse',str(uuid4()),HASH)
    with pytest.raises(ProcessingJobConflict, match='request_key_conflict'):
        PostgresProcessingCancellationStore(pool).cancel(owner,other.job_id,key,1)
    stores[0].cancel(owner,other.job_id)


def test_lease_reclaim_rejects_stale_cancel_and_late_worker(db, stores, pool):
    owner, _, job = setup_job(db, stores)
    first = stores[0].claim('first')
    assert first.job_id == job.job_id
    cancel = PostgresProcessingCancellationStore(pool)
    with pytest.raises(ProcessingJobConflict,match='revision_conflict'):
        cancel.cancel(owner,job.job_id,str(uuid4()),1)
    stores[0].heartbeat(owner,job.job_id,first.lease_token)
    assert db('SELECT revision FROM server_processing_jobs WHERE job_id=%s',(job.job_id,))[0]['revision'] == 2
    db("UPDATE server_processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",(job.job_id,))
    second = stores[0].claim('second')
    assert second.job_id == job.job_id and second.lease_token != first.lease_token
    with pytest.raises(ProcessingJobConflict,match='revision_conflict'):
        cancel.cancel(owner,job.job_id,str(uuid4()),2)
    assert cancel.cancel(owner,job.job_id,str(uuid4()),3)['revision'] == 4
    for token in (first.lease_token,second.lease_token):
        with pytest.raises(ProcessingJobLeaseLost):
            stores[0].succeed(owner,job.job_id,token)
        with pytest.raises(ProcessingJobLeaseLost):
            stores[0].fail(owner,job.job_id,token,error_code='synthetic_failure',retryable=True)
    assert db('SELECT status,revision FROM server_processing_jobs WHERE job_id=%s',(job.job_id,)) == [{'status':'cancelled','revision':4}]


def test_foreign_owner_missing_deleted_and_finished_are_rejected(db, stores, pool):
    owner,paper,job = setup_job(db,stores)
    cancel = PostgresProcessingCancellationStore(pool)
    for user, target in [('other',job.job_id),(owner,str(uuid4()))]:
        with pytest.raises(ProcessingJobNotFound):
            cancel.cancel(user,target,str(uuid4()),1)
    stores[0].cancel(owner,job.job_id)
    with pytest.raises(ProcessingJobConflict,match='not_cancellable'):
        cancel.cancel(owner,job.job_id,str(uuid4()),2)
    db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE paper_id=%s',(paper,))
    with pytest.raises(ProcessingJobNotFound):
        cancel.cancel(owner,job.job_id,str(uuid4()),2)
    assert not db('SELECT * FROM server_processing_cancel_requests WHERE owner_user_id=%s',(owner,))


def test_published_result_is_never_cancelled(db, stores, pool):
    remote = PgvectorContractStore(db, stores, pool)
    first = remote.publish()
    row = db('SELECT j.job_id,j.revision FROM server_processing_jobs j JOIN server_processing_publications p ON p.owner_user_id=j.owner_user_id AND p.source_job_id=j.job_id WHERE p.publication_id=%s',(first.publication,))[0]
    with pytest.raises(ProcessingJobConflict,match='not_cancellable'):
        PostgresProcessingCancellationStore(pool).cancel(remote.owner,str(row['job_id']),str(uuid4()),row['revision'])
    assert db('SELECT active_publication_id FROM server_processing_versions WHERE owner_user_id=%s AND paper_id=%s',(remote.owner,first.paper))[0]['active_publication_id'] == UUID(first.publication)


def test_ledger_failure_rolls_back_cancellation(db, stores, pool):
    owner,_,job = setup_job(db,stores)
    with pool.connection() as conn:
        conn.execute("CREATE FUNCTION reject_cancel_test() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic'; END $$")
        conn.execute('CREATE TRIGGER reject_cancel_test BEFORE INSERT ON server_processing_cancel_requests FOR EACH ROW EXECUTE FUNCTION reject_cancel_test()')
    try:
        with pytest.raises(Exception,match='synthetic'):
            PostgresProcessingCancellationStore(pool).cancel(owner,job.job_id,str(uuid4()),1)
        assert db('SELECT status,revision FROM server_processing_jobs WHERE job_id=%s',(job.job_id,)) == [{'status':'queued','revision':1}]
    finally:
        db('DROP TRIGGER reject_cancel_test ON server_processing_cancel_requests')
        db('DROP FUNCTION reject_cancel_test()')
        stores[0].cancel(owner,job.job_id)


def test_publication_and_cancel_have_only_one_winner(db, stores, pool, monkeypatch):
    from threading import Barrier
    from solo_leveling.interfaces.http.processing_publications import ProcessingPublicationNotFound
    remote = PgvectorContractStore(db, stores, pool)
    gate = Barrier(2)
    original = stores[1].publish
    def publish(*args, **kwargs):
        gate.wait(timeout=10)
        return original(*args, **kwargs)
    monkeypatch.setattr(stores[1], 'publish', publish)
    with ThreadPoolExecutor(max_workers=2) as executor:
        publication = executor.submit(remote.publish)
        # 게시 직전 준비가 끝난 실행의 ID는 barrier에 도착한 뒤 조회한다.
        import time
        deadline = time.monotonic() + 10
        while gate.n_waiting != 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert gate.n_waiting == 1
        job = db('SELECT job_id,revision FROM server_processing_jobs WHERE owner_user_id=%s',(remote.owner,))[0]
        def cancel():
            gate.wait(timeout=10)
            return PostgresProcessingCancellationStore(pool).cancel(remote.owner,str(job['job_id']),str(uuid4()),job['revision'])
        cancelled = executor.submit(cancel)
        try:
            publication.result(timeout=15)
            published = True
        except ProcessingPublicationNotFound:
            published = False
        try:
            cancelled.result(timeout=15)
            did_cancel = True
        except ProcessingJobConflict:
            did_cancel = False
    assert published != did_cancel
    state = db('SELECT status FROM server_processing_jobs WHERE job_id=%s',(job['job_id'],))[0]['status']
    assert state == ('succeeded' if published else 'cancelled')
    rows = db('SELECT publication_id FROM server_processing_publications WHERE owner_user_id=%s',(remote.owner,))
    assert len(rows) == int(published)
