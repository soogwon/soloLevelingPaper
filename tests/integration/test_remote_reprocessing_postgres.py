"""합성 PDF의 검토·동의·원자 접수와 예산 반환. provider와 worker는 연결하지 않는다."""
# ruff: noqa: F811
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.fixtures.pgvector_retrieval_contract import PgvectorContractStore
from solo_leveling.interfaces.http.remote_reprocessing import PostgresReprocessingReviews, NOTICE

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='로컬 PostgreSQL 필요')
CONFIG=dict(policy_version='recovery-test-v1',window_key='test',provider='synthetic',model='synthetic',
            max_calls=2,max_input_tokens=4000,max_output_tokens=1000,input_usd_per_million='1',
            output_usd_per_million='2',review_seconds=3600)


def setup(db,stores,pool):
    remote=PgvectorContractStore(db,stores,pool)
    version=remote.publish(partial=True)
    owner=remote.owner
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,'합성')",(owner,))
    db('''INSERT INTO server_pdf_uploads(upload_id,owner_user_id,paper_id,server_version_id,
        file_name,file_size,content_type,sha256_hex,object_key,status,expires_at,source_kind)
        VALUES (%s,%s,%s,'v1','synthetic.pdf',100,'application/pdf',%s,%s,'ready',clock_timestamp()+interval '1 day','remote')''',
        (str(uuid4()),owner,version.paper,'a'*64,'private/test/'+str(uuid4())))
    db("INSERT INTO server_dispatch_controls(control_id,generation,intake_enabled,dispatch_enabled) VALUES ('provider_dispatch',1,true,false) ON CONFLICT(control_id) DO UPDATE SET intake_enabled=true")
    # 서로 다른 테스트의 예산을 공유하지 않는다.
    config=dict(CONFIG,window_key=str(uuid4()))
    for scope,id_ in [('owner',owner),('service','service')]:
        db('''INSERT INTO server_dispatch_budget_limits(scope_type,scope_id,policy_version,window_key,max_input_tokens,max_output_tokens)
            VALUES (%s,%s,%s,%s,8000,2000)''',(scope,id_,config['policy_version'],config['window_key']))
    return remote,version,PostgresReprocessingReviews(pool,config=config),str(uuid4())


def grant(service,owner,key):
    for purpose in ('storage','ai_provider'):service.grant(owner,key,purpose,NOTICE)


def test_web_intake_concurrent_replay_cancel_and_no_dispatch(db,stores,pool):
    remote,v,service,key=setup(db,stores,pool);owner=remote.owner
    initial=db('SELECT count(*) n FROM server_processing_jobs')[0]['n']
    assert service.draft(owner,v.context,'reparse')['contract']['max_cost_usd']=='0.006'
    assert service.view(owner,key) is None
    prepared=service.prepare(owner,key,v.context,'reparse')
    assert prepared['storage']=='missing'
    with pytest.raises(ValueError):service.submit(owner,key)
    grant(service,owner,key)
    with ThreadPoolExecutor(3) as workers:
        results=list(workers.map(lambda _:service.submit(owner,key),range(3)))
    assert len({r['job_id'] for r in results})==1
    assert all(r['state']=='queued' and not r['execution_enabled'] for r in results)
    restored=PostgresReprocessingReviews(pool,config={k:service.contract[k] for k in CONFIG}).view(owner,key)
    assert restored['job_id']==results[0]['job_id']
    reserved=db('SELECT reserved_input_tokens n FROM server_dispatch_budget_limits WHERE window_key=%s',(service.contract['window_key'],))
    assert [r['n'] for r in reserved]==[4000,4000]
    assert service.view('other',key) is None
    assert service.submit('other',key) is None
    assert service.cancel('other',key) is None
    assert service.cancel(owner,key,'ai_provider')['state']=='cancelled'
    assert service.cancel(owner,key)['job_id']==results[0]['job_id']
    assert service.submit(owner,key)['state']=='cancelled'
    assert all(r['n']==0 for r in db('SELECT reserved_input_tokens n FROM server_dispatch_budget_limits WHERE window_key=%s',(service.contract['window_key'],)))
    assert db('SELECT count(*) n FROM server_processing_jobs')[0]['n']==initial
    assert not db('SELECT 1 FROM server_provider_attempts')
    assert remote.active_embedding(v)==v.embedding


@pytest.mark.parametrize('change',['expired','revoked','policy','pdf','publication','budget','paused','deleted'])
def test_stale_or_unauthorized_admission_rolls_back(db,stores,pool,change):
    remote,v,service,key=setup(db,stores,pool);owner=remote.owner
    service.prepare(owner,key,v.context,'translation_recovery');grant(service,owner,key)
    if change=='expired':db("UPDATE server_reprocessing_reviews SET created_at=clock_timestamp()-interval '2 hours',expires_at=clock_timestamp()-interval '1 hour' WHERE request_key=%s",(key,))
    if change=='revoked':
        db('UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE owner_user_id=%s AND purpose=%s',(owner,'ai_provider'))
    if change=='policy':service.contract['model']='changed'
    if change=='pdf':db("UPDATE server_pdf_uploads SET status='deleted' WHERE owner_user_id=%s",(owner,))
    if change=='publication':remote.publish(100,base=v)
    if change=='budget':db('UPDATE server_dispatch_budget_limits SET max_input_tokens=0 WHERE window_key=%s',(service.contract['window_key'],))
    if change=='paused':db("UPDATE server_dispatch_controls SET intake_enabled=false WHERE control_id='provider_dispatch'")
    if change=='deleted':db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE paper_id=%s',(v.paper,))
    if change=='deleted':assert service.submit(owner,key) is None
    else:
        with pytest.raises(ValueError):service.submit(owner,key)
    assert db('SELECT job_id FROM server_reprocessing_reviews WHERE request_key=%s',(key,))[0]['job_id'] is None
    assert all(r['n']==0 for r in db('SELECT reserved_input_tokens n FROM server_dispatch_budget_limits WHERE window_key=%s',(service.contract['window_key'],)))


def test_same_key_different_operation_and_invalid_notice(db,stores,pool):
    remote,v,service,key=setup(db,stores,pool)
    service.prepare(remote.owner,key,v.context,'reparse')
    with pytest.raises(ValueError):service.prepare(remote.owner,key,v.context,'translation_recovery')
    with pytest.raises(ValueError):service.grant(remote.owner,key,'storage','other-notice')
    assert service.view(remote.owner,key)['storage']=='missing'


def test_context_cleanup_returns_reservation_once(db,stores,pool):
    remote,v,service,key=setup(db,stores,pool)
    service.prepare(remote.owner,key,v.context,'reparse');grant(service,remote.owner,key)
    service.submit(remote.owner,key)
    db('DELETE FROM server_execution_contexts WHERE owner_user_id=%s AND context_id=%s',(remote.owner,v.context))
    assert not db('SELECT 1 FROM server_reprocessing_reviews WHERE request_key=%s',(key,))
    assert all(r['n']==0 for r in db('SELECT reserved_input_tokens n FROM server_dispatch_budget_limits WHERE window_key=%s',(service.contract['window_key'],)))
