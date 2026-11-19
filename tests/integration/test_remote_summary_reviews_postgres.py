"""요약 준비와 목적별 동의·만료를 격리 DB에서 검증한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4
import pytest

from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_summary_intake_postgres import setup
from solo_leveling.interfaces.http.remote_summary_reviews import PostgresSummaryReviews
from solo_leveling.interfaces.http.remote_executions import RemoteExecutionConflict

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def review(pool, db, stores):
    request, _, _, policy = setup(db, stores, pool)
    config = {k:request.unpack()[2][k] for k in ('provider','model','prompt_version','language','budget_policy_version')}
    service = PostgresSummaryReviews(pool,settings=config,policy=policy,window_key='test',
                                    review_seconds=60,web_origin='https://example.invalid')
    key, context = str(uuid4()), request.unpack()[0].context_id
    service.prepare('alice',key,context)
    return service,key,context


def test_preparation_requires_two_new_consents_and_concurrent_submit_is_single(pool,db,stores):
    service,key,context = review(pool,db,stores)
    view = service.view('alice',key)
    assert view['storage'] == view['ai_provider'] == 'missing'
    assert view['automatic_generation'] is False
    assert 'sources' not in view
    with pytest.raises(ValueError,match='consent_required'):
        service.submit('alice',key)
    service.grant('alice',key,'storage','remote-summary-v1')
    with pytest.raises(ValueError,match='consent_required'):
        service.submit('alice',key)
    service.grant('alice',key,'ai_provider','remote-summary-v1')
    with ThreadPoolExecutor(max_workers=3) as workers:
        results = list(workers.map(lambda _:service.submit('alice',key),range(3)))
    assert len({r['execution_id'] for r in results}) == 1
    restored = service.view('alice', key)
    assert restored['execution_id'] == results[0]['execution_id']
    assert restored['state'] == 'queued'
    service.revoke('alice',key,'ai_provider')
    assert service.submit('alice',key)['execution_id'] == results[0]['execution_id']
    assert service.view('alice',key)['ai_provider'] == 'revoked'
    assert service.prepare('alice',key,context)['status'] == 'review_required'
    assert db('SELECT count(*) n FROM server_provider_attempts')[0]['n'] == 0


@pytest.mark.parametrize('change',['expired','revoked','model','notice'])
def test_invalid_review_cannot_create_execution(pool,db,stores,change):
    service,key,_ = review(pool,db,stores)
    for purpose in ('storage','ai_provider'):
        service.grant('alice',key,purpose,'remote-summary-v1')
    if change == 'expired':
        db("UPDATE server_summary_reviews SET created_at=clock_timestamp()-interval '2 minutes',expires_at=clock_timestamp()-interval '1 minute' WHERE request_key=%s",(key,))
    elif change == 'revoked':
        service.revoke('alice',key,'ai_provider')
        service.grant('alice',key,'ai_provider','remote-summary-v1')
        assert service.view('alice',key)['ai_provider'] == 'revoked'
    elif change == 'model':
        service.settings['model'] = 'different'
    else:
        with pytest.raises(ValueError,match='notice_changed'):
            service.grant('alice',key,'ai_provider','remote-question-v1')
        return
    with pytest.raises(ValueError):
        service.submit('alice',key)
    assert not db('SELECT 1 FROM server_remote_executions WHERE request_key=%s',(key,))


def test_foreign_owner_and_changed_context_are_rejected(pool,db,stores):
    service,key,context = review(pool,db,stores)
    assert service.prepare('bob',key,context) is None
    assert service.view('bob',key) is None
    assert service.grant('bob',key,'storage','remote-summary-v1') is None
    assert service.revoke('bob',key,'storage') is None
    assert service.submit('bob',key) is None
    _,_,other_context = review(pool,db,stores)
    with pytest.raises(RemoteExecutionConflict):
        service.prepare('alice',key,other_context)
