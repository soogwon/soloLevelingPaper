"""실제 처리 작업과 동의·lease·사용량 원장의 결합을 격리 DB에서 검증한다."""
# ruff: noqa: F811
from dataclasses import replace
import os

import pytest

from solo_leveling.application.provider_dispatch import AttemptRequest
from solo_leveling.interfaces.http.processing_jobs import PostgresProcessingJobStore
from solo_leveling.workers.processing_authorization import PostgresProcessingAuthorization
from tests.integration.test_remote_processing_worker_postgres import claimed_source
from tests.integration.test_remote_processing_postgres import pool, db, new_id  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL DSN 필요')


def setup_authorization(pool, db):
    job, _, _, _ = claimed_source(pool, db)
    auth = PostgresProcessingAuthorization(pool)
    source = auth.load(job).source
    policy = new_id()
    db("""INSERT INTO server_dispatch_budget_limits
        (scope_type,scope_id,policy_version,window_key,max_input_tokens,max_output_tokens)
        VALUES ('service','service',%s,'test',1000,1000),('owner',%s,%s,'test',1000,1000)""",
       (policy, job.owner_user_id, policy))
    ledger = auth.ledger(source, auth.allowed, policy_version=policy, window_key='test')
    intent = auth.dispatch_intent(source)
    return auth, source, ledger, intent


@pytest.mark.parametrize('state', ['reserved', 'unknown', 'overrun'])
def test_unsettled_usage_blocks_new_stages_and_survives_new_adapter(pool, db, state):
    auth, source, ledger, intent = setup_authorization(pool, db)
    permit = ledger.authorize(AttemptRequest(intent, 'first', 'translation', 100, 50))
    assert permit is not None
    if state == 'unknown': ledger.unknown(permit.attempt_id)
    if state == 'overrun': ledger.observe(permit.attempt_id, 101, 50)
    for phase in ('parse', 'translation', 'embedding', 'result'):
        assert not PostgresProcessingAuthorization(pool).allowed(source, phase)
    assert ledger.authorize(AttemptRequest(intent, 'second', 'translation', 100, 50)) is None
    if state != 'overrun':
        ledger.observe(permit.attempt_id, 80, 40)
        assert auth.allowed(source, 'embedding')
        assert ledger.authorize(AttemptRequest(intent, 'first', 'translation', 100, 50)) is None
        assert ledger.authorize(AttemptRequest(intent, 'second', 'translation', 100, 50)) is not None


@pytest.mark.parametrize('field', ['owner_user_id', 'execution_id', 'lease_token', 'input_hash',
                                    'storage_consent_id', 'ai_consent_id', 'notice_version'])
def test_changed_dispatch_identity_never_reserves(pool, db, field):
    _, _, ledger, intent = setup_authorization(pool, db)
    assert ledger.authorize(AttemptRequest(replace(intent, **{field: new_id()}), 'first', 'translation', 100, 50)) is None


@pytest.mark.parametrize('change', ['cancel', 'deleted', 'revoked', 'lease', 'dispatch'])
def test_current_authority_changes_block_existing_ledger(pool, db, change):
    auth, source, ledger, intent = setup_authorization(pool, db)
    if change == 'cancel':
        PostgresProcessingJobStore(pool).cancel(source.owner_user_id, source.job_id)
    elif change == 'deleted':
        db('UPDATE server_papers SET deleted_at=CURRENT_TIMESTAMP WHERE paper_id=%s', (source.paper_id,))
    elif change == 'revoked':
        db('UPDATE server_remote_consents SET revoked_at=CURRENT_TIMESTAMP WHERE consent_id=%s', (intent.ai_consent_id,))
    elif change == 'lease':
        db("UPDATE server_processing_jobs SET lease_expires_at=CURRENT_TIMESTAMP-interval '1 second' WHERE job_id=%s", (source.job_id,))
    else:
        db("UPDATE server_dispatch_controls SET dispatch_enabled=false")
    assert not auth.allowed(source, 'translation')
    assert ledger.authorize(AttemptRequest(intent, 'first', 'translation', 100, 50)) is None
    assert db('SELECT count(*) AS n FROM server_provider_attempts WHERE execution_id=%s', (source.job_id,))[0]['n'] == 0

    if change == 'lease':
        PostgresProcessingJobStore(pool).cancel(source.owner_user_id, source.job_id)


@pytest.mark.parametrize('stage', ['generation', 'repair', 'coverage', 'semantic_verification'])
def test_processing_ledger_rejects_non_translation_stages(pool, db, stage):
    _, source, ledger, intent = setup_authorization(pool, db)
    assert ledger.authorize(AttemptRequest(intent, 'other-stage', stage, 100, 50)) is None
    assert db('SELECT count(*) AS n FROM server_provider_attempts WHERE execution_id=%s',
              (source.job_id,))[0]['n'] == 0
