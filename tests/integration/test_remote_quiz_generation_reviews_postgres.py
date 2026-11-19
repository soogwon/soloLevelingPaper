"""퀴즈 생성 준비와 목적별 동의·만료를 격리 DB에서 검증한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4
import pytest

from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_quiz_intake_postgres import setup
from solo_leveling.interfaces.http.remote_quiz_generation_reviews import PostgresQuizGenerationReviews
from solo_leveling.interfaces.http.remote_executions import RemoteExecutionConflict

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def review(pool, db, stores):
    request, _, _, policy = setup(db, stores, pool)
    config = {k:request.unpack()[2][k] for k in ('provider','model','prompt_version','language','budget_policy_version')}
    service = PostgresQuizGenerationReviews(pool,settings=config,policy=policy,window_key='test',
                                    review_seconds=60,web_origin='https://example.invalid')
    key = str(uuid4())
    prepare(service,key,request)
    return service,key,request



def prepare(service,key,request,owner='alice'):
    binding,sources,config=request.unpack()
    return service.prepare(owner,key,binding.context_id,tuple(s.evidence_id for s in sources),
        step=config['step'],question_count=config['question_count'],question_type=config['question_type'])


def grant(service,key,request,purpose):
    return service.grant('alice',key,purpose,'remote-quiz-generation-v1',request.input_hash)


def test_preparation_requires_two_new_consents_and_concurrent_submit_is_single(pool,db,stores):
    service,key,request = review(pool,db,stores)
    view = service.view('alice',key)
    assert view['storage'] == view['ai_provider'] == 'missing'
    assert view['automatic_generation'] is False
    assert 'sources' not in view
    with pytest.raises(ValueError,match='consent_required'):
        service.submit('alice',key)
    grant(service,key,request,'storage')
    with pytest.raises(ValueError,match='consent_required'):
        service.submit('alice',key)
    grant(service,key,request,'ai_provider')
    with ThreadPoolExecutor(max_workers=3) as workers:
        results = list(workers.map(lambda _:service.submit('alice',key),range(3)))
    assert len({r['execution_id'] for r in results}) == 1
    restored = service.view('alice', key)
    assert restored['execution_id'] == results[0]['execution_id']
    assert restored['state'] == 'queued'
    service.revoke('alice',key,'ai_provider')
    assert service.submit('alice',key)['execution_id'] == results[0]['execution_id']
    assert service.view('alice',key)['ai_provider'] == 'revoked'
    assert prepare(service,key,request)['status'] == 'review_required'
    assert db('SELECT count(*) n FROM server_provider_attempts')[0]['n'] == 0


@pytest.mark.parametrize('change',['expired','revoked','model','notice'])
def test_invalid_review_cannot_create_execution(pool,db,stores,change):
    service,key,request = review(pool,db,stores)
    for purpose in ('storage','ai_provider'):
        grant(service,key,request,purpose)
    if change == 'expired':
        db("UPDATE server_quiz_generation_reviews SET created_at=clock_timestamp()-interval '2 minutes',expires_at=clock_timestamp()-interval '1 minute' WHERE request_key=%s",(key,))
    elif change == 'revoked':
        service.revoke('alice',key,'ai_provider')
        grant(service,key,request,'ai_provider')
        assert service.view('alice',key)['ai_provider'] == 'revoked'
    elif change == 'model':
        service.settings['model'] = 'different'
    else:
        with pytest.raises(ValueError,match='notice_changed'):
            service.grant('alice',key,'ai_provider','remote-question-v1',request.input_hash)
        return
    with pytest.raises(ValueError):
        service.submit('alice',key)
    assert not db('SELECT 1 FROM server_remote_executions WHERE request_key=%s',(key,))


def test_foreign_owner_and_changed_context_are_rejected(pool,db,stores):
    service,key,request = review(pool,db,stores)
    assert prepare(service,key,request,owner='bob') is None
    assert service.view('bob',key) is None
    assert service.grant('bob',key,'storage','remote-quiz-generation-v1',request.input_hash) is None
    assert service.revoke('bob',key,'storage') is None
    assert service.submit('bob',key) is None
    _,_,other_request = review(pool,db,stores)
    with pytest.raises(RemoteExecutionConflict):
        prepare(service,key,other_request)


def test_review_cancels_only_its_execution_without_new_generation(pool,db,stores):
    service,key,request = review(pool,db,stores)
    assert service.cancel('alice', key)['state'] == 'not_submitted'
    for purpose in ('storage','ai_provider'):
        grant(service,key,request,purpose)
    submitted = service.submit('alice', key)
    assert service.cancel('bob', key) is None
    cancelled = service.cancel('alice', key)
    assert cancelled['state'] == 'cancel_requested'
    assert cancelled['execution_id'] == submitted['execution_id']
    assert service.cancel('alice', key)['state'] == 'cancel_requested'
    assert service.view('alice', key)['state'] == 'cancel_requested'
    assert not db('SELECT 1 FROM server_provider_attempts WHERE execution_id=%s',(submitted['execution_id'],))


def test_expiry_does_not_renew_and_existing_receipt_can_still_be_recovered(pool,db,stores):
    service,key,request=review(pool,db,stores)
    for purpose in ('storage','ai_provider'):grant(service,key,request,purpose)
    saved=service.submit('alice',key)
    db("UPDATE server_quiz_generation_reviews SET created_at=clock_timestamp()-interval '2 minutes',expires_at=clock_timestamp()-interval '1 minute' WHERE request_key=%s",(key,))
    before=service.view('alice',key)
    assert before['active'] is False
    prepare(service,key,request)
    assert service.view('alice',key)['expires_at']==before['expires_at']
    assert service.submit('alice',key)==saved
    with pytest.raises(ValueError,match='expired'):
        grant(service,key,request,'storage')
    assert service.revoke('alice',key,'storage')['storage']=='revoked'
    assert service.cancel('alice',key)['state']=='cancel_requested'
    assert all(r['reserved_input_tokens']==0 for r in db('SELECT reserved_input_tokens FROM server_dispatch_budget_limits WHERE policy_version=%s',(service.policy.version,)))


def test_stale_hash_or_changed_policy_does_not_grant(pool,db,stores):
    from dataclasses import replace
    service,key,request=review(pool,db,stores)
    with pytest.raises(ValueError,match='changed'):
        service.grant('alice',key,'storage','remote-quiz-generation-v1','stale')
    service.policy=replace(service.policy,max_input_tokens=999)
    with pytest.raises(ValueError,match='changed'):
        grant(service,key,request,'storage')
    assert service.view('alice',key)['storage']=='missing'


def test_expiry_during_reservation_rolls_back_all_admission(pool,db,stores,monkeypatch):
    from solo_leveling.interfaces.http.remote_quiz_intake import PostgresQuizIntake
    service,key,request=review(pool,db,stores)
    for purpose in ('storage','ai_provider'):grant(service,key,request,purpose)
    db("UPDATE server_quiz_generation_reviews SET expires_at=clock_timestamp()+interval '0.15 seconds' WHERE request_key=%s",(key,))
    original=PostgresQuizIntake.submit
    def delayed(self,*args,**kwargs):
        result=original(self,*args,**kwargs)
        with self.pool.connection() as conn:conn.execute('SELECT pg_sleep(0.2)')
        return result
    monkeypatch.setattr(PostgresQuizIntake,'submit',delayed)
    with pytest.raises(ValueError,match='expired'):
        service.submit('alice',key)
    assert not db('SELECT 1 FROM server_remote_executions WHERE request_key=%s',(key,))
    assert not db("SELECT 1 FROM server_quiz_generation_inputs WHERE input_snapshot->>'budget_policy_version'=%s",(service.policy.version,))
    assert all(r['reserved_input_tokens']==0 for r in db('SELECT reserved_input_tokens FROM server_dispatch_budget_limits WHERE policy_version=%s',(service.policy.version,)))


def test_expiry_during_grant_rolls_back_consent(pool,db,stores,monkeypatch):
    service,key,request=review(pool,db,stores)
    original=service._active
    calls=0
    def delayed(conn,row):
        nonlocal calls
        calls+=1
        if calls==2:conn.execute('SELECT pg_sleep(0.2)')
        return original(conn,row)
    monkeypatch.setattr(service,'_active',delayed)
    db("UPDATE server_quiz_generation_reviews SET expires_at=clock_timestamp()+interval '0.15 seconds' WHERE request_key=%s",(key,))
    before=db('SELECT count(*) n FROM server_remote_consents')[0]['n']
    with pytest.raises(ValueError,match='expired'):grant(service,key,request,'storage')
    assert db('SELECT count(*) n FROM server_remote_consents')[0]['n']==before
    assert service.view('alice',key)['storage']=='missing'


def test_waiting_for_review_lock_does_not_use_stale_expiry(pool,db,stores,monkeypatch):
    from threading import Event
    service,key,request=review(pool,db,stores)
    started=Event();original=service._row
    def waiting(conn,owner,request_key):
        started.set()
        return original(conn,owner,request_key)
    monkeypatch.setattr(service,'_row',waiting)
    db("UPDATE server_quiz_generation_reviews SET expires_at=clock_timestamp()+interval '0.2 seconds' WHERE request_key=%s",(key,))
    with ThreadPoolExecutor(max_workers=1) as workers:
        with pool.connection() as conn:
            conn.execute('SELECT 1 FROM server_quiz_generation_reviews WHERE request_key=%s FOR UPDATE',(key,))
            future=workers.submit(grant,service,key,request,'storage')
            assert started.wait(2)
            conn.execute('SELECT pg_sleep(0.25)')
        with pytest.raises(ValueError,match='expired'):future.result(timeout=5)
    assert service.view('alice',key)['storage']=='missing'


@pytest.mark.parametrize('change',['context','input','consents','policy'])
def test_existing_execution_with_same_key_cannot_be_misbound_or_cancelled(pool,db,stores,change):
    from solo_leveling.interfaces.http.remote_quiz_intake import PostgresQuizIntake
    service,key,request=review(pool,db,stores)
    if change=='context':
        other,storage,ai,policy=setup(db,stores,pool)
        settings={k:other.unpack()[2][k] for k in service.settings}
    else:
        from dataclasses import replace
        from solo_leveling.application.quiz.remote_contract import RemoteQuizGenerationInput
        binding,sources,config=request.unpack()
        config={k:v for k,v in config.items() if k not in ('schema_version','notice_version','operation','binding','sources','max_calls')}
        if change=='input':config['question_count']=2
        other=RemoteQuizGenerationInput.create(binding,sources,**config)
        settings=service.settings;policy=replace(service.policy)
        storage,ai=str(uuid4()),str(uuid4())
        for cid,purpose in [(storage,'storage'),(ai,'ai_provider')]:
            db("INSERT INTO server_remote_consents(consent_id,owner_user_id,purpose,input_hash,notice_version) VALUES (%s,'alice',%s,%s,'remote-quiz-generation-v1')",(cid,purpose,other.input_hash))
    if change=='policy':
        for purpose in ('storage','ai_provider'):grant(service,key,request,purpose)
        ids=db('SELECT storage_consent_id,ai_consent_id FROM server_quiz_generation_reviews WHERE request_key=%s',(key,))[0]
        storage,ai=ids['storage_consent_id'],ids['ai_consent_id']
        policy=replace(policy,timeout_seconds=121)
    saved=PostgresQuizIntake(pool,settings=settings).submit('alice',key,other,storage,ai,policy=policy,window_key='test')
    assert saved is not None
    with pytest.raises(RemoteExecutionConflict):grant(service,key,request,'storage')
    for action in (service.view,service.submit,service.cancel):
        with pytest.raises(RemoteExecutionConflict):action('alice',key)
    assert db('SELECT state FROM server_remote_executions WHERE execution_id=%s',(saved.execution_id,))[0]['state']=='queued'
