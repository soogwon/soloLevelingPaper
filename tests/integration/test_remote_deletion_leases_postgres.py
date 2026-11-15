"""실제 DB에서 삭제 lease 회수·세대 검사·단계 재개를 검증한다. backend는 합성이다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock, patch
import time
import os
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, make_paper, new_id  # noqa: F401
from solo_leveling.interfaces.http.remote_deletions import PostgresRemoteDeletionStore, RemoteDeletionCleanupService
from solo_leveling.domain.deletion_policy import DeletionPolicy
pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='실제 PostgreSQL DSN 필요')


@pytest.fixture
def case(pool,db):
    owner='delete-'+new_id()
    paper=make_paper(db,owner)
    store=PostgresRemoteDeletionStore(pool)
    request=store.request(owner,paper,new_id())
    yield store,request
    db('DELETE FROM server_deletion_requests WHERE request_id=%s',(request.request_id,))


def expire(db,cleanup):
    db("UPDATE server_deletion_requests SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE request_id=%s",(cleanup.request_id,))


def test_restart_reclaims_expiry_and_rejects_all_late_old_worker_actions(case,db,pool):
    store,request=case
    old=store.claim_cleanup('old',lease_seconds=60)
    assert store.claim_cleanup('other',lease_seconds=60) is None
    expire(db,old)
    current=PostgresRemoteDeletionStore(pool).claim_cleanup('new',lease_seconds=60)
    assert current.request_id==old.request_id and current.attempt_count==old.attempt_count+1
    assert current.lease_token!=old.lease_token
    operation=Mock()
    for action in [lambda:store.mark_cleanup_complete(old),lambda:store.mark_cleanup_failed(old,'physical_cleanup_failed'),
                   lambda:store.heartbeat_cleanup(old,lease_seconds=60),lambda:store.run_cleanup_phase(old,'derived',operation)]:
        with pytest.raises(ValueError,match='remote_deletion_cleanup_stale'): action()
    operation.assert_not_called()
    store.run_cleanup_phase(current,'derived',operation)
    store.run_cleanup_phase(current,'source',operation)
    store.mark_cleanup_complete(current)
    assert db('SELECT status FROM server_deletion_requests WHERE request_id=%s',(request.request_id,))[0]['status']=='complete'


def test_expired_heartbeat_cannot_revive_old_lease(case,db):
    store,_=case
    old=store.claim_cleanup('old',lease_seconds=60)
    store.heartbeat_cleanup(old,lease_seconds=120)
    expire(db,old)
    with pytest.raises(ValueError,match='stale'): store.heartbeat_cleanup(old,lease_seconds=60)


@pytest.mark.parametrize('completed_phases',[0,1,2])
def test_crash_or_lost_completion_resumes_only_unfinished_phases(case,db,pool,completed_phases):
    store,_=case
    old=store.claim_cleanup('old',lease_seconds=60)
    for phase in ['derived','source'][:completed_phases]: store.run_cleanup_phase(old,phase,lambda:None)
    # 완료 응답 유실 또는 프로세스 중단을 모사한다. 서비스와 저장소를 새로 만든다.
    expire(db,old)
    backend=Mock()
    result=RemoteDeletionCleanupService(PostgresRemoteDeletionStore(pool),backend,DeletionPolicy(31,30,30),
        worker_id='new',lease_seconds=60).run_once()
    assert result=={'status':'complete','attempt_count':2}
    assert backend.delete_derived.call_count==(completed_phases==0)
    assert backend.delete_source.call_count==(completed_phases<2)


def test_phase_failure_preserves_completed_derived_and_retries_source(case,pool):
    store,_=case
    backend=Mock(); backend.delete_source.side_effect=RuntimeError('private')
    service=RemoteDeletionCleanupService(store,backend,DeletionPolicy(31,30,30),worker_id='worker',lease_seconds=60)
    assert service.run_once()['status']=='failed'
    backend.delete_source.side_effect=None
    assert service.run_once()['status']=='complete'
    assert backend.delete_derived.call_count==1 and backend.delete_source.call_count==2


def test_held_phase_lock_blocks_concurrent_reclaim_even_when_time_expires(case,pool):
    store,_=case
    old=store.claim_cleanup('old',lease_seconds=1)
    entered,release=Event(),Event()
    def operation():
        entered.set()
        assert release.wait(10)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future=executor.submit(store.run_cleanup_phase,old,'derived',operation)
        try:
            assert entered.wait(10)
            time.sleep(1.1)
            assert PostgresRemoteDeletionStore(pool).claim_cleanup('new',lease_seconds=60) is None
        finally:
            release.set()
        with pytest.raises(ValueError,match='stale'): future.result()
    assert store.claim_cleanup('new',lease_seconds=60).attempt_count==2


def test_completion_requires_both_phases_and_live_generation(case):
    store,_=case
    cleanup=store.claim_cleanup('worker',lease_seconds=60)
    with pytest.raises(ValueError,match='incomplete'):store.mark_cleanup_complete(cleanup)
    with pytest.raises(ValueError,match='phase_invalid'):store.run_cleanup_phase(cleanup,'source',lambda:None)


def test_completion_record_failure_can_resume_without_repeating_backend(case,db):
    store,request=case
    backend=Mock()
    service=RemoteDeletionCleanupService(store,backend,DeletionPolicy(31,30,30),worker_id='worker',lease_seconds=60)
    with patch.object(store,'mark_cleanup_complete',side_effect=RuntimeError('synthetic db failure')):
        with pytest.raises(RuntimeError):service.run_once()
    db("UPDATE server_deletion_requests SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE request_id=%s",(request.request_id,))
    assert service.run_once()=={'status':'complete','attempt_count':2}
    assert backend.delete_derived.call_count==backend.delete_source.call_count==1


def test_legacy_cleaning_without_lease_is_reclaimed(case,db):
    store,request=case
    db("UPDATE server_deletion_requests SET status='cleaning' WHERE request_id=%s",(request.request_id,))
    assert store.claim_cleanup('worker',lease_seconds=60).request_id==request.request_id
