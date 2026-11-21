"""채팅 요약은 고정 근거와 사용자 확인을 거쳐 저장한다."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.application.summary.remote_export import export_summary
from solo_leveling.interfaces.http.remote_executions import RemoteExecutionConflict
from solo_leveling.interfaces.http.remote_summary_reviews import PostgresSummaryReviews
from tests.integration.test_remote_processing_postgres import db, pool, stores  # noqa: F401
from tests.integration.test_remote_retrieval_postgres import seed


def test_chat_summary_requires_web_confirmation_and_keeps_pinned_citations(pool, db, stores):
    _, revision, binding = seed(db, stores, pool, 'alice')
    _, foreign_revision, _ = seed(db, stores, pool, 'bob')
    _, other_revision, _ = seed(db, stores, pool, 'alice')
    policy = RemoteExecutionPolicy('test', 120, 60, 1, 4000, 1024)
    service = PostgresSummaryReviews(pool, settings=dict(provider='openai', model='synthetic',
        prompt_version='remote-summary-v2', language='ko', budget_policy_version=policy.version),
        policy=policy, window_key='test', review_seconds=60, web_origin='https://example.invalid')
    key, context, chunk = str(uuid4()), binding.context_id, str(revision[3][0])
    item = dict(kind='method', text='합성 논문은 두 사례를 비교했다.', chunk_ids=[chunk])
    for unrelated in (foreign_revision, other_revision):
        with pytest.raises(ValueError, match='chat_summary_evidence_invalid'):
            service.prepare_chat('alice', key, context, [dict(item, chunk_ids=[str(unrelated[3][0])])])
    assert service.prepare_chat('bob', key, context, [item]) is None
    prepared = service.prepare_chat('alice', key, context, [item])
    assert prepared['evidence_pages'] == {chunk: 6}
    assert service.prepare_chat('alice', key, context, [item]) == prepared
    with pytest.raises(RemoteExecutionConflict):
        service.prepare_chat('alice', key, context, [dict(item, text='변경한 요약')])
    assert service.latest('alice', context) is None
    assert service.result('alice', key) is None
    assert service.review_chat('bob', key) is None
    assert service.save_chat('bob', key) is None
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert all(entry['saved'] for entry in workers.map(lambda _: service.save_chat('alice', key), range(2)))
    saved = service.result('alice', key)
    assert service.latest('alice', context) == saved
    assert service.latest('bob', context) is None
    assert service.contexts('alice', binding.paper_id, binding.server_version_id)['items'][0]['context_id'] == context
    assert 'PDF 페이지: 6' in export_summary(saved)['markdown']
    assert service.evidence('alice', key, chunk)['pdf_page'] == 6
    assert service.evidence('bob', key, chunk) is None
    expired_key = str(uuid4())
    service.prepare_chat('alice', expired_key, context, [item])
    db("UPDATE server_chat_summaries SET created_at=clock_timestamp()-interval '2 minutes' WHERE request_key=%s",
       (expired_key,))
    assert not service.review_chat('alice', expired_key)['active']
    with pytest.raises(RemoteExecutionConflict, match='chat_summary_expired'):
        service.save_chat('alice', expired_key)
    db("UPDATE server_papers SET deleted_at=clock_timestamp() WHERE owner_user_id=%s AND paper_id=%s",
       ('alice', binding.paper_id))
    assert service.latest('alice', context) is None
    assert service.evidence('alice', key, chunk) is None
    assert db('SELECT count(*) AS count FROM server_remote_executions')[0]['count'] == 0


def test_account_storage_consent_saves_chat_summary_and_can_be_unsaved(pool, db, stores):
    from solo_leveling.interfaces.http.account_consents import NOTICE_VERSION, PostgresAccountConsents
    _, revision, binding = seed(db, stores, pool, 'alice')
    policy = RemoteExecutionPolicy('test', 120, 60, 1, 4000, 1024)
    service = PostgresSummaryReviews(pool, settings=dict(provider='openai', model='synthetic',
        prompt_version='remote-summary-v2', language='ko', budget_policy_version=policy.version),
        policy=policy, window_key='test', review_seconds=60, web_origin='https://example.invalid')
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES ('alice','합성') ON CONFLICT DO NOTHING")
    store = PostgresAccountConsents(pool)
    store.grant('alice', ['storage'], NOTICE_VERSION)
    try:
        key, context, chunk = str(uuid4()), binding.context_id, str(revision[3][0])
        item = dict(kind='result', text='합성 결과 문장이다.', chunk_ids=[chunk])
        prepared = service.prepare_chat('alice', key, context, [item])
        assert prepared['status'] == 'saved' and prepared['review_url'].endswith(key)
        assert service.review_chat('alice', key)['saved'] is True
        assert service.latest('alice', context)['request_key'] == key
        assert service.unsave_chat('bob', key) is None
        assert service.unsave_chat('alice', key)['saved'] is False
        assert service.latest('alice', context) is None
        assert service.save_chat('alice', key)['saved'] is True
    finally:
        store.revoke('alice', 'storage')
