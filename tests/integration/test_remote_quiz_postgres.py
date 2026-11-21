"""검수 합성 퀴즈의 정답 비노출과 제출 멱등성을 실제 로컬 DB로 확인한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4
import os
import pytest
from tests.integration.test_remote_processing_postgres import pool,db,stores  # noqa: F401
from tests.integration.test_remote_answer_store_postgres import setup_answer
from solo_leveling.interfaces.http.remote_answers import PostgresRemoteAnswerStore
from solo_leveling.interfaces.http.remote_quiz import PostgresRemoteQuiz,NOTICE
from solo_leveling.interfaces.http.remote_executions import RemoteExecutionConflict
from solo_leveling.domain.quiz import QuizQuestion

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='격리 PostgreSQL 필요')


def setup(pool,db,stores):
    intent,prepared=setup_answer(db,stores,pool)
    PostgresRemoteAnswerStore(pool).save(intent,prepared)
    context=prepared.binding.context_id; quiz=str(uuid4())
    questions=(QuizQuestion('multiple_choice','합성 문항',(prepared.evidence[0].evidence_id,),('가','나'),0),)
    service=PostgresRemoteQuiz(pool)
    view=service.store_reviewed('alice',context,quiz,'preview',questions)
    consent=str(uuid4())
    db('''INSERT INTO server_remote_consents(consent_id,owner_user_id,purpose,input_hash,notice_version)
        VALUES (%s,'alice','storage',%s,%s)''',(consent,view['storage_input_hash'],NOTICE))
    return service,context,quiz,questions,consent


def test_quiz_hides_answers_and_concurrent_submit_is_one_attempt(pool,db,stores):
    service,context,quiz,_,consent=setup(pool,db,stores)
    view=service.get('alice',context,quiz)
    assert set(view['questions'][0])=={'position','question_type','prompt','choices'}
    key=str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as workers:
        rows=list(workers.map(lambda _:service.submit('alice',context,quiz,key,{0:0},consent),range(4)))
    assert all(r==rows[0] for r in rows)
    assert rows[0]['score']==1
    assert service.scores('alice',context)[0]['attempts']==1
    assert PostgresRemoteQuiz(pool).attempt('alice',context,rows[0]['attempt_id'])==rows[0]
    with pytest.raises(RemoteExecutionConflict):service.submit('alice',context,quiz,key,{0:1},consent)
    second=service.submit('alice',context,quiz,str(uuid4()),{0:1},consent)
    assert second['score']==0 and second['results'][0]['correct_answer']=='가'
    assert service.scores('alice',context)[0]['latest_score']==0
    assert service.scores('alice',context)[0]['best_score']==1
    first_page=service.history('alice',context,limit=1)
    second_page=service.history('alice',context,limit=1,cursor=first_page['next_cursor'])
    assert {first_page['items'][0]['attempt_id'],second_page['items'][0]['attempt_id']}=={rows[0]['attempt_id'],second['attempt_id']}
    assert second_page['next_cursor'] is None


def test_fixed_quiz_cannot_be_replaced_and_foreign_access_is_hidden(pool,db,stores):
    service,context,quiz,questions,consent=setup(pool,db,stores)
    with pytest.raises(RemoteExecutionConflict):
        service.store_reviewed('alice',context,quiz,'preview',(replace(questions[0],correct_index=1),))
    assert service.get('bob',context,quiz) is None
    assert service.submit('bob',context,quiz,str(uuid4()),{0:0},consent) is None
    assert service.history('bob',context) is None
    assert service.scores('bob',context) is None
    with pytest.raises(ValueError,match='evidence_invalid'):
        service.store_reviewed('alice',context,str(uuid4()),'preview',(replace(questions[0],evidence_ids=(str(uuid4()),)),))


def test_revoked_consent_blocks_new_attempt_but_restores_old(pool,db,stores):
    service,context,quiz,_,consent=setup(pool,db,stores)
    key=str(uuid4());saved=service.submit('alice',context,quiz,key,{0:0},consent)
    db('UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE consent_id=%s',(consent,))
    assert service.submit('alice',context,quiz,key,{0:0},consent)==saved
    with pytest.raises(ValueError,match='consent_required'):
        service.submit('alice',context,quiz,str(uuid4()),{0:0},consent)


def test_direct_quiz_mutation_is_denied_and_short_answer_uses_local_grader(pool,db,stores):
    import psycopg
    service,context,quiz,questions,_=setup(pool,db,stores)
    with pytest.raises(psycopg.errors.CheckViolation):
        db("UPDATE server_remote_quizzes SET snapshot='{}' WHERE quiz_id=%s",(quiz,))
    other=str(uuid4())
    q=QuizQuestion('short_answer','합성 단답형',questions[0].evidence_ids,answer='Attention',grading_criteria='정규화 일치')
    view=service.store_reviewed('alice',context,other,'review',(q,))
    assert 'grading_criteria' not in view['questions'][0]
    consent=str(uuid4())
    db('''INSERT INTO server_remote_consents(consent_id,owner_user_id,purpose,input_hash,notice_version)
        VALUES (%s,'alice','storage',%s,%s)''',(consent,view['storage_input_hash'],NOTICE))
    saved=service.submit('alice',context,other,str(uuid4()),{0:' attention! '},consent)
    assert saved['score']==1
    wrong=service.submit('alice',context,other,str(uuid4()),{0:'설명 문장'},consent)
    assert wrong['score']==0


def test_quiz_storage_consent_is_explicit_pinned_and_idempotent(pool,db,stores):
    service,context,quiz,questions,old=setup(pool,db,stores)
    service.revoke_storage('alice',context,quiz)
    view=service.consent('alice',context,quiz)
    assert view['storage']=='missing'
    assert 'questions' not in view and 'answer' not in view
    with pytest.raises(RemoteExecutionConflict):
        service.grant_storage('alice',context,quiz,NOTICE,'0'*64)
    with pytest.raises(ValueError):
        service.grant_storage('alice',context,quiz,'remote-question-v1',view['quiz_revision'])
    with ThreadPoolExecutor(max_workers=4) as workers:
        rows=list(workers.map(lambda _:service.grant_storage('alice',context,quiz,NOTICE,view['quiz_revision']),range(4)))
    assert all(r['consent_id']==rows[0]['consent_id'] for r in rows)
    assert rows[0]['consent_id']!=old
    key=str(uuid4());saved=service.submit('alice',context,quiz,key,{0:0},rows[0]['consent_id'])
    assert service.revoke_storage('bob',context,quiz) is None
    assert service.grant_storage('bob',context,quiz,NOTICE,view['quiz_revision']) is None
    assert service.consent('bob',context,quiz) is None
    service.revoke_storage('alice',context,quiz)
    with pytest.raises(ValueError,match='consent_required'):
        service.submit('alice',context,quiz,str(uuid4()),{0:0},rows[0]['consent_id'])
    assert service.submit('alice',context,quiz,key,{0:0},rows[0]['consent_id'])==saved
    other=str(uuid4());service.store_reviewed('alice',context,other,'preview',questions)
    assert service.consent('alice',context,other)['storage']=='missing'


def test_saved_quiz_card_links_submission_score_and_exact_source(pool, db, stores, monkeypatch):
    from types import SimpleNamespace
    from solo_leveling.interfaces.http.remote_evidence_read import PostgresRemoteEvidenceReader
    from solo_leveling.interfaces.mcp import remote_quiz
    from tests.unit.test_remote_annotation_tools import Server, token

    service, context, quiz, questions, consent = setup(pool, db, stores)
    owner = 'alice'
    reader = PostgresRemoteEvidenceReader(pool)
    def evidence(ctx, eid):
        value = reader.get(owner, ctx, eid)
        return {'status': 'ok', 'result': value} if value else {'status': 'not_found'}

    monkeypatch.setattr(remote_quiz, 'get_access_token', lambda: token(['quizzes:read', 'quizzes:write']))
    server = Server()
    remote_quiz.register_remote_quiz(server, SimpleNamespace(owner=lambda: owner, evidence=evidence), service)
    opened = server.tools['show_remote_quiz'](context, quiz)['result']
    assert 'correct_answer' not in opened['quiz']['questions'][0]
    key = str(uuid4())
    bound = opened['binding']
    submit = server.tools['submit_remote_quiz_card']
    saved = submit(context, quiz, key, {'0': 1}, consent, bound)['result']['attempt']
    assert submit(context, quiz, key, {'0': 1}, consent, bound)['result']['attempt'] == saved
    review = server.tools['get_remote_quiz_card_review']
    value = review(context, quiz, saved['attempt_id'], bound)['result']
    assert value['scores'][0]['attempts'] == 1 and value['scores'][0]['latest_score'] == 0
    assert value['evidence'][0]['evidence_id'] == questions[0].evidence_ids[0]
    assert value['evidence'][0]['quote_original']
    assert review(context, str(uuid4()), saved['attempt_id'], bound)['status'] == 'invalid_argument'
    owner = 'bob'
    assert review(context, quiz, saved['attempt_id'], bound)['status'] == 'invalid_argument'


def test_account_storage_consent_submits_until_revoked_for_this_quiz(pool,db,stores):
    from solo_leveling.interfaces.http.account_consents import NOTICE_VERSION,PostgresAccountConsents
    service,context,quiz,_,consent=setup(pool,db,stores)
    db("DELETE FROM server_remote_consents WHERE consent_id=%s",(consent,))
    assert service.consent('alice',context,quiz)['storage']=='missing'
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES ('alice','합성') ON CONFLICT DO NOTHING")
    account=PostgresAccountConsents(pool)
    account.grant('alice',['storage'],NOTICE_VERSION)
    try:
        view=service.consent('alice',context,quiz)
        assert view['storage']=='current' and view['account'] is True
        assert service.submit('alice',context,quiz,str(uuid4()),{0:0},view['consent_id'])['score']==1
        with pytest.raises(ValueError,match='consent_required'):
            service.submit('alice',context,quiz,str(uuid4()),{0:0},str(uuid4()))
        revoked=service.revoke_storage('alice',context,quiz)
        assert revoked['storage']=='missing' and revoked['account'] is False
        with pytest.raises(ValueError,match='consent_required'):
            service.submit('alice',context,quiz,str(uuid4()),{0:0},view['consent_id'])
    finally:
        account.revoke('alice','storage')
