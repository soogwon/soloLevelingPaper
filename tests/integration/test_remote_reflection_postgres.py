"""원격 성찰의 고정 질문·동시 저장·판본과 소유권을 실제 로컬 DB로 검증한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import os
import pytest
from tests.integration.test_remote_processing_postgres import pool,db,stores  # noqa: F401
from tests.integration.test_remote_retrieval_postgres import seed
from solo_leveling.interfaces.http.remote_reflection import PostgresRemoteReflection,NOTICE
from solo_leveling.interfaces.http.remote_executions import RemoteExecutionConflict

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='격리 PostgreSQL 필요')


def setup(pool,db,stores):
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES ('alice','합성 사용자') ON CONFLICT DO NOTHING")
    _,_,binding=seed(db,stores,pool,'alice')
    service=PostgresRemoteReflection(pool)
    view=service.questions('alice',binding.context_id)
    consent=str(uuid4())
    db('''INSERT INTO server_remote_consents(consent_id,owner_user_id,purpose,input_hash,notice_version)
        VALUES (%s,'alice','storage',%s,%s)''',(consent,view['storage_input_hash'],NOTICE))
    args=dict(question_revision=view['question_revision'],template_id='before.hypothesis',body='합성 가설',
              status='completed',expected_revision=0,storage_consent_id=consent)
    return service,binding,args


def test_concurrent_replay_and_revision_conflict(pool,db,stores):
    service,binding,args=setup(pool,db,stores); context=binding.context_id;key=str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as workers:
        results=list(workers.map(lambda _:service.save('alice',context,key,**args),range(4)))
    assert all(r==results[0] for r in results)
    assert results[0]['revision']==1
    with pytest.raises(RemoteExecutionConflict):
        service.save('alice',context,str(uuid4()),**args)
    with pytest.raises(RemoteExecutionConflict):
        service.save('alice',context,key,**dict(args,body='다른 본문'))
    changed=service.save('alice',context,str(uuid4()),**dict(args,expected_revision=1,body='수정 답변'))
    assert changed['revision']==2
    assert PostgresRemoteReflection(pool).save('alice',context,key,**args)==results[0]
    assert service.questions('alice',context)['answers'][0]['body']=='수정 답변'
    assert db('SELECT count(*) n FROM server_reflection_requests WHERE context_id=%s',(context,))[0]['n']==2


def test_comparison_uses_saved_answers_only_and_pins_templates(pool,db,stores,monkeypatch):
    service,binding,args=setup(pool,db,stores); context=binding.context_id
    service.save('alice',context,str(uuid4()),**args)
    import solo_leveling.interfaces.http.remote_reflection as module
    monkeypatch.setattr(module,'question_set',lambda:dict(questions=[],groups=[]))
    comparison=service.comparison('alice',context)
    group=next(g for g in comparison['groups'] if g['group_id']=='hypothesis_vs_paper')
    assert group['before']['answer']['body']=='합성 가설'
    assert all(i['answer'] is None for i in group['after'])
    assert len(service.questions('alice',context)['questions'])==7


@pytest.mark.parametrize('change',['owner','revision','revoked','purpose','notice'])
def test_rejected_storage_leaves_no_answer(pool,db,stores,change):
    service,binding,args=setup(pool,db,stores);context=binding.context_id
    owner='bob' if change=='owner' else 'alice'
    if change=='revision':args['question_revision']='different'
    if change=='revoked':db('UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE consent_id=%s',(args['storage_consent_id'],))
    if change=='purpose':db("UPDATE server_remote_consents SET purpose='ai_provider' WHERE consent_id=%s",(args['storage_consent_id'],))
    if change=='notice':db("UPDATE server_remote_consents SET notice_version='remote-question-v1' WHERE consent_id=%s",(args['storage_consent_id'],))
    if change=='owner':
        assert service.save(owner,context,str(uuid4()),**args) is None
        assert service.questions(owner,context) is None
    else:
        with pytest.raises((ValueError,RemoteExecutionConflict)):
            service.save(owner,context,str(uuid4()),**args)
    assert not db('SELECT 1 FROM server_reflection_answers WHERE context_id=%s',(context,))


def test_delete_scrubs_history_and_old_key_cannot_resurrect_answer(pool,db,stores):
    service,binding,args=setup(pool,db,stores);context=binding.context_id;key=str(uuid4())
    service.save('alice',context,key,**args)
    assert service.export('bob',context) is None
    assert service.export('alice',context)['request_history'][0]['body']=='합성 가설'
    with pytest.raises(RemoteExecutionConflict):
        service.delete_answer('alice',context,args['template_id'],2)
    assert service.delete_answer('bob',context,args['template_id'],1) is None
    assert service.delete_answer('alice',context,args['template_id'],1)['state']=='deleted'
    assert service.save('alice',context,key,**args)['state']=='deleted'
    exported=service.export('alice',context)
    assert exported['current']['answers'][0]['status']=='deleted'
    assert exported['current']['answers'][0]['revision']==2
    assert '합성 가설' not in str(exported)
    assert service.save('alice',context,str(uuid4()),**dict(args,expected_revision=2,body='새 답변'))['revision']==3
    with pytest.raises(RemoteExecutionConflict):
        service.delete_answer('alice',context,args['template_id'],1)


def test_web_storage_consent_is_context_scoped_and_revocable(pool,db,stores):
    service,binding,args=setup(pool,db,stores);context=binding.context_id
    service.revoke_storage('alice',context)
    assert service.consent('alice',context)['storage']=='missing'
    with pytest.raises(ValueError):
        service.grant_storage('alice',context,'remote-question-v1',args['question_revision'])
    assert service.grant_storage('bob',context,NOTICE,args['question_revision']) is None
    with ThreadPoolExecutor(max_workers=3) as workers:
        consents=list(workers.map(lambda _:service.grant_storage('alice',context,NOTICE,args['question_revision']),range(3)))
    assert len({c['consent_id'] for c in consents})==1
    args['storage_consent_id']=consents[0]['consent_id']
    assert service.save('alice',context,str(uuid4()),**args)['revision']==1
    service.revoke_storage('alice',context)
    with pytest.raises(ValueError):
        service.save('alice',context,str(uuid4()),**dict(args,expected_revision=1))


def test_account_storage_consent_saves_until_revoked_for_this_revision(pool,db,stores):
    from solo_leveling.interfaces.http.account_consents import NOTICE_VERSION,PostgresAccountConsents
    service,binding,args=setup(pool,db,stores);context=binding.context_id
    service.revoke_storage('alice',context)
    assert service.consent('alice',context)['storage']=='missing'
    db("DELETE FROM server_remote_consents WHERE owner_user_id='alice' AND input_hash=%s",
       (NOTICE+':'+args['question_revision'],))
    account=PostgresAccountConsents(pool)
    account.grant('alice',['storage'],NOTICE_VERSION)
    try:
        view=service.consent('alice',context)
        assert view['storage']=='current' and view['account'] is True
        saved=service.save('alice',context,str(uuid4()),**dict(args,storage_consent_id=view['consent_id']))
        assert saved['revision']==1
        with pytest.raises(ValueError,match='consent_required'):
            service.save('alice',context,str(uuid4()),**dict(args,expected_revision=1,storage_consent_id=str(uuid4())))
        # 이 판에서 철회하면 계정 동의가 남아 있어도 저장하지 않는다.
        revoked=service.revoke_storage('alice',context)
        assert revoked['storage']=='missing' and revoked['account'] is False
        with pytest.raises(ValueError,match='consent_required'):
            service.save('alice',context,str(uuid4()),**dict(args,expected_revision=1,storage_consent_id=view['consent_id']))
    finally:
        account.revoke('alice','storage')
