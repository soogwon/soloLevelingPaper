"""저장된 인용에서만 준비하고 다른 계정·손상 근거를 재사용하지 않는다."""
# ruff: noqa: F811
import os
from uuid import uuid4
from dataclasses import replace
import pytest
from tests.integration.test_remote_processing_postgres import pool,db,stores  # noqa: F401
from tests.integration.test_remote_answer_store_postgres import setup_answer
from solo_leveling.interfaces.http.remote_answers import PostgresRemoteAnswerStore
from solo_leveling.interfaces.http.remote_quiz_preparation import PostgresQuizPreparation
from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='격리 PostgreSQL 필요')


def test_preparation_reads_only_pinned_saved_quotes(pool,db,stores):
    intent,prepared=setup_answer(db,stores,pool)
    PostgresRemoteAnswerStore(pool).save(intent,prepared)
    policy=RemoteExecutionPolicy(version='quiz-v1',timeout_seconds=120,stage_timeout_seconds=60,max_calls=1,max_input_tokens=4000,max_output_tokens=1024)
    service=PostgresQuizPreparation(pool,settings=dict(provider='synthetic',model='fixed',prompt_version='v1',
        language='ko',budget_policy_version=policy.version),policy=policy)
    context=prepared.binding.context_id;eid=prepared.evidence[0].evidence_id
    def prepare(owner='alice',ids=(eid,)):
        return service.prepare(owner,context,ids,step='preview',question_count=1,question_type='multiple_choice')
    first=prepare();assert first.input_hash==prepare().input_hash
    assert first.unpack()[1][0].evidence_id==eid
    assert prepare('bob') is None and prepare(ids=(str(uuid4()),)) is None
    with pytest.raises(ValueError):prepare(ids=(eid,eid))
    assert db("SELECT count(*) AS n FROM server_remote_executions WHERE operation='generate_quiz'")[0]['n']==0
    with pytest.raises(ValueError):
        PostgresQuizPreparation(pool,settings=service.settings,policy=replace(policy,max_calls=2))

    db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE owner_user_id=%s AND paper_id=%s',('alice',prepared.binding.paper_id))
    assert prepare() is None
