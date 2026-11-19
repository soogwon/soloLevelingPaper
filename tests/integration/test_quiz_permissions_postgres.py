"""전용 퀴즈 역할로 준비·생성·제출과 금지 권한을 실제 로컬 DB에서 검증한다."""
# ruff: noqa: F811
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores, DSN  # noqa: F401
from tests.integration.test_remote_quiz_intake_postgres import setup
from solo_leveling.interfaces.cli.quiz_permissions import grant_quiz_role
from solo_leveling.interfaces.http.remote_quiz_generation_reviews import PostgresQuizGenerationReviews
from solo_leveling.interfaces.http.remote_quiz import PostgresRemoteQuiz
from solo_leveling.workers.remote_quiz import PersistedQuizExecutor, RemoteQuizRunner
from solo_leveling.infrastructure.generation.openai_generator import OpenAIGenerationSettings
pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')

@pytest.fixture(scope='module')
def restricted(pool):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    runtimes = []
    roles = []
    try:
        for worker in (False, True):
            role = 'quiz_test_' + uuid4().hex[:12]
            with pool.connection() as conn:
                schema = conn.execute('SELECT current_schema() AS s').fetchone()['s']
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'local-test-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(sql.Identifier(role)))
                grant_quiz_role(conn,schema,role,worker=worker)
                grant_quiz_role(conn,schema,role,worker=worker)
            roles.append(role)
            runtimes.append(ConnectionPool(make_conninfo(DSN,user=role,password='local-test-only'),
                kwargs=dict(row_factory=dict_row,options=f'-c search_path={schema}'),min_size=1,max_size=4,open=True))
        yield runtimes, roles, schema
    finally:
        for runtime in runtimes:
            runtime.close()
        with psycopg.connect(DSN) as conn:
            for role in roles:
                conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def review(db, stores, pool, runtime):
    request, _, _, policy = setup(db, stores, pool)
    binding, sources, config = request.unpack()
    config = {k:config[k] for k in ('provider','model','prompt_version','language','budget_policy_version')}
    config.update(provider='openai',prompt_version='remote-quiz-generation-v1')
    service = PostgresQuizGenerationReviews(runtime,settings=config,policy=policy,window_key='test',review_seconds=60,web_origin='https://example.test')
    key = str(uuid4())
    assert service.prepare('alice',key,binding.context_id,(sources[0].evidence_id,),step='preview',question_count=1,question_type='multiple_choice')['status']=='review_required'
    for purpose in ('storage','ai_provider'):
        assert service.grant('alice',key,purpose,'remote-quiz-generation-v1',service.view('alice',key)['input_hash'])[purpose]=='current'
    return service,key,binding.context_id,sources[0].evidence_id,policy


def test_restricted_generation_submission_and_restore(pool,db,stores,restricted):
    runtimes,_,_ = restricted
    service,key,context,evidence,policy = review(db,stores,pool,runtimes[0])
    saved = service.submit('alice',key)
    assert service.submit('alice',key)==saved
    item=dict(question_type='multiple_choice',prompt='합성 질문?',choices=['첫째','둘째'],correct_index=0,
              answer='첫째',grading_criteria='',evidence_ids=[evidence])
    envelope=dict(status='completed',output=[dict(type='message',role='assistant',status='completed',
        content=[dict(type='output_text',text=json.dumps({'items':[item]}))])],usage=dict(input_tokens=20,output_tokens=10))
    post=Mock(return_value=SimpleNamespace(status_code=200,json=lambda:envelope,close=Mock()))
    execute=PersistedQuizExecutor(runtimes[1],settings=OpenAIGenerationSettings('synthetic',model='fixed',max_output_tokens=100,allow_external_api=True),
        token_upper_bound=lambda *_:100,window_key='test',validate_semantics=lambda *_:True,post=post)
    runner=RemoteQuizRunner(runtimes[1],execute,policy_version=policy.version,window_key='test')
    assert runner.run_once()=='finished'
    assert runner.run('alice',saved['execution_id'])=='not_claimed'
    assert post.call_count==1
    quiz=PostgresRemoteQuiz(runtimes[0])
    result=quiz.generation_result('alice',context,key)
    assert result['state']=='completed'
    quiz_id=result['quiz']['quiz_id']
    assert 'correct_index' not in json.dumps(quiz.get('alice',context,quiz_id))
    consent=quiz.consent('alice',context,quiz_id)
    consent=quiz.grant_storage('alice',context,quiz_id,consent['notice_version'],consent['quiz_revision'])
    attempt_key=str(uuid4())
    attempt=quiz.submit('alice',context,quiz_id,attempt_key,{0:1},consent['consent_id'])
    assert quiz.attempt_by_key('alice',context,quiz_id,attempt_key)['attempt']==attempt
    assert attempt['correct_count']==0
    assert quiz.attempt_by_key('bob',context,quiz_id,attempt_key) is None
    quiz.revoke_storage('alice',context,quiz_id)
    assert quiz.consent('alice',context,quiz_id)['storage']=='missing'


@pytest.mark.parametrize('mode',['cancel','revoke'])
def test_restricted_stop_without_dispatch(pool,db,stores,restricted,mode):
    runtimes,_,_=restricted
    service,key,_,_,policy=review(db,stores,pool,runtimes[0]);saved=service.submit('alice',key)
    if mode=='cancel':service.cancel('alice',key)
    else:service.revoke('alice',key,'ai_provider')
    execute=Mock(side_effect=AssertionError('전송 금지'))
    assert RemoteQuizRunner(runtimes[1],execute).run('alice',saved['execution_id'])==('cancelled' if mode=='cancel' else 'blocked')
    execute.assert_not_called()
    assert all(r['reserved_input_tokens']==0 for r in db('SELECT reserved_input_tokens FROM server_dispatch_budget_limits WHERE policy_version=%s',(policy.version,)))

def test_forbidden_mutations_and_role_reuse(pool,db,stores,restricted):
    import psycopg
    runtimes,roles,schema = restricted
    service,key,_,_,_ = review(db,stores,pool,runtimes[0])
    service.submit('alice',key)
    for index,runtime in enumerate(runtimes):
        statements = ['SELECT * FROM web_sessions','SELECT * FROM server_device_credentials',
            'CREATE TABLE forbidden(id int)','DELETE FROM server_remote_executions',
            'UPDATE server_dispatch_budget_limits SET max_input_tokens=999999',
            'UPDATE server_processing_chunks SET original_text=original_text',
            'UPDATE server_papers SET paper_id=paper_id',
            'UPDATE server_dispatch_controls SET control_id=control_id']
        statements += (['UPDATE server_remote_consents SET revoked_at=clock_timestamp()'] if index else [
            "UPDATE server_remote_executions SET state='running'",
            'UPDATE server_provider_attempts SET status=status'])
        with runtime.connection() as conn:
            denied_insert = ('server_remote_quiz_attempts' if index else 'server_remote_quizzes')
            assert not conn.execute("SELECT has_table_privilege(current_user,%s,'INSERT') AS allowed", (schema+'.'+denied_insert,)).fetchone()['allowed']
            for statement in statements:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with conn.transaction():
                        conn.execute(statement)
        with pool.connection() as conn:
            with pytest.raises(ValueError,match='quiz_role_profile_changed'):
                grant_quiz_role(conn,schema,roles[index],worker=not bool(index))


def test_other_operation_and_consent_are_not_mutable(pool,db,stores,restricted):
    import psycopg
    from tests.integration.test_question_admission_postgres import configured
    submit,_ = configured(db,stores,pool)
    question = submit(str(uuid4()))
    assert question is not None
    runtimes,_,_ = restricted
    for runtime in runtimes:
        with runtime.connection() as conn:
            for statement in (
                'UPDATE server_remote_executions SET updated_at=clock_timestamp() WHERE execution_id=%s',
                'UPDATE server_question_budget_reservations SET remaining_input_tokens=0 WHERE execution_id=%s',
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege,match='quiz_scope_invalid'):
                    with conn.transaction():
                        conn.execute(statement,(question.execution_id,))
    with runtimes[0].connection() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege,match='quiz_scope_invalid'):
            with conn.transaction():
                conn.execute("UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE notice_version='notice'")
    assert db('SELECT state FROM server_remote_executions WHERE execution_id=%s',(question.execution_id,))[0]['state']=='queued'


def test_privileged_role_is_rejected_before_grants(pool):
    with pool.connection() as conn:
        role = conn.execute('SELECT current_user AS r').fetchone()['r']
        schema = conn.execute('SELECT current_schema() AS s').fetchone()['s']
        with pytest.raises(ValueError,match='quiz_role_not_isolated'):
            grant_quiz_role(conn,schema,role)


def test_revoked_consent_cannot_be_reactivated(pool,db,stores,restricted):
    import psycopg
    service,key,_,_,_ = review(db,stores,pool,restricted[0][0])
    service.revoke('alice',key,'ai_provider')
    assert service.revoke('alice',key,'ai_provider')['ai_provider']=='revoked'
    with restricted[0][0].connection() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege,match='quiz_scope_invalid'):
            with conn.transaction():
                conn.execute("UPDATE server_remote_consents SET revoked_at=NULL WHERE consent_id=(SELECT ai_consent_id FROM server_quiz_generation_reviews WHERE request_key=%s)",(key,))
    assert service.view('alice',key)['ai_provider']=='revoked'
