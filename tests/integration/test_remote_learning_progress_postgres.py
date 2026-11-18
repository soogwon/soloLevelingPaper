"""실제 DB에서 위치·상태 재전송과 오래된 기기의 덮어쓰기를 차단한다."""
# ruff: noqa: F811
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_processing_publication_postgres import prepared, publish
from solo_leveling.interfaces.http.remote_learning import PostgresRemoteLearningStore, RemoteLearningConflict

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


@pytest.fixture
def context(pool, db):
    publisher, source, digest = prepared(pool, db)
    publication = publish(publisher, source, digest)
    key = str(uuid4())
    db("""INSERT INTO server_execution_contexts
        (context_id,owner_user_id,paper_id,server_version_id,publication_id,parse_revision_id)
        VALUES (%s,%s,%s,%s,%s,%s)""", (key,source.owner_user_id,source.paper_id,
            source.version_id,publication.publication_id,source.parse_revision_id))
    return source.owner_user_id,key,None


def setup(pool, db, context):
    owner, key, _ = context
    store = PostgresRemoteLearningStore(pool)
    store.start(owner, key)
    return store, owner, key


def test_concurrent_same_request_replays_and_late_retry_returns_current(pool, db, context):
    store, owner, key = setup(pool, db, context)
    args = dict(request_key=str(uuid4()),status='completed',pdf_page=2,expected_revision=1)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(lambda _:store.set_progress(owner,key,**args),range(2)))
    assert sorted(r['replayed'] for r in results)==[False,True]
    assert {r['applied_revision'] for r in results}=={2}
    assert store.reading_state(owner,key)['last_position']=={'pdf_page':2}
    reopened=store.set_progress(owner,key,request_key=str(uuid4()),status='reading',pdf_page=None,expected_revision=2)
    assert reopened['state']['revision']==3
    late=PostgresRemoteLearningStore(pool).set_progress(owner,key,**args)
    assert late['replayed'] and late['applied_revision']==2
    assert late['state']['status']=='reading' and late['state']['revision']==3
    assert late['state']['last_pdf_page'] is None
    assert db('SELECT count(*) n FROM server_remote_learning_requests WHERE owner_user_id=%s',(owner,))[0]['n']==2


def test_revision_payload_page_and_owner_fail_without_writes(pool, db, context):
    store,owner,key=setup(pool,db,context)
    args=dict(request_key=str(uuid4()),status='reading',pdf_page=1,expected_revision=1)
    assert store.set_progress('other',key,**args) is None
    with pytest.raises(ValueError):store.set_progress(owner,key,**{**args,'pdf_page':4})
    assert store.get(owner,key)['revision']==1
    store.set_progress(owner,key,**args)
    with pytest.raises(RemoteLearningConflict):store.set_progress(owner,key,**{**args,'pdf_page':2})
    with pytest.raises(RemoteLearningConflict):store.set_progress(owner,key,**{**args,'request_key':str(uuid4())})
    assert store.get(owner,key)['revision']==2
    db('UPDATE server_papers SET deleted_at=CURRENT_TIMESTAMP WHERE owner_user_id=%s',(owner,))
    assert store.set_progress(owner,key,**args) is None
