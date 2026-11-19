"""웹 조회에서 소유자·판본·삭제와 고정 저장 결과를 확인한다."""
# ruff: noqa: F811
import os
from uuid import uuid4
import pytest
from tests.integration.test_remote_processing_postgres import pool,db,stores  # noqa: F401
from tests.integration.test_remote_summary_intake_postgres import setup,submit,completed_attempt
from solo_leveling.interfaces.http.remote_summary_results import PostgresSummaryResults
pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='격리 PostgreSQL 필요')


def test_web_contexts_only_show_success_of_owner_and_version(pool,db,stores):
    args=setup(db,stores,pool);key=str(uuid4());row=submit(pool,args,key)
    b,sources,_=args[0].unpack();service=PostgresSummaryResults(pool)
    assert service.contexts('alice',b.paper_id,b.server_version_id)['items']==[]
    intent=completed_attempt(pool,args,row)
    service.complete(intent,[dict(kind='method',text='합성 요약',evidence_ids=[sources[0].evidence_id])])
    page=service.contexts('alice',b.paper_id,b.server_version_id)
    assert page['items']==[dict(context_id=b.context_id,paper_id=b.paper_id,server_version_id=b.server_version_id,publication_id=b.publication_id)]
    assert page['next_cursor'] is None
    assert service.contexts('alice',b.paper_id,b.server_version_id,b.context_id)['items']==[]
    assert service.contexts('bob',b.paper_id,b.server_version_id)['items']==[]
    assert service.contexts('alice',b.paper_id,'other-version')['items']==[]
    assert service.latest('alice',b.context_id)['request_key']==key
    assert len(db('SELECT * FROM server_provider_attempts WHERE execution_id=%s',(row.execution_id,)))==1
    db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE paper_id=%s',(b.paper_id,))
    assert service.contexts('alice',b.paper_id,b.server_version_id)['items']==[]
    assert service.latest('alice',b.context_id) is None
