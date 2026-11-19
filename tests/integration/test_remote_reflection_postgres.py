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
