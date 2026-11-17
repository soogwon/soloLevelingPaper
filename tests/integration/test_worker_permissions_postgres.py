"""실제 DB worker 전체 흐름과 권한 차단. 외부 처리 결과만 합성 대역이다."""
# ruff: noqa: F811
import os
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, DSN, new_id  # noqa: F401
from tests.integration.test_remote_processing_intake_postgres import admitted_source
from tests.fixtures.worker_permissions import grant_worker, LOCK_ONLY
from tests.fixtures.processing_result import result, LIMITS, SETTINGS, SPEC
from solo_leveling.application.provider_dispatch import AttemptRequest
from solo_leveling.interfaces.http.remote_processing_intake import PostgresRemoteProcessingIntake
from solo_leveling.interfaces.http.remote_upload_consents import NOTICE_VERSION
from solo_leveling.interfaces.http.object_storage import DownloadedObject
from solo_leveling.workers.verification import VerificationConfig, run_once

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 DB 필요')


@pytest.fixture(scope='module')
def restricted(pool):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    role = 'worker_test_' + uuid4().hex[:12]
    with pool.connection() as conn:
        schema = conn.execute('SELECT current_schema() AS s').fetchone()['s']
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'local-test-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(sql.Identifier(role)))
        grant_worker(conn, schema, role)
    runtime = ConnectionPool(make_conninfo(DSN, user=role, password='local-test-only'),
        kwargs=dict(row_factory=dict_row, options=f'-c search_path={schema}'), min_size=1, max_size=4, open=True)
    try:
        yield runtime, role, schema
    finally:
        runtime.close()
        with psycopg.connect(DSN) as conn:
            conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@pytest.mark.parametrize('mode', ['published', 'unknown', 'revoked'])
def test_restricted_worker_claim_usage_staging_publication(pool, db, restricted, monkeypatch, mode):
    import solo_leveling.workers.processing_composition as composition
    from solo_leveling.interfaces.http.processing_jobs import PostgresProcessingJobStore
    runtime, role, schema = restricted
    owner, intent, storage, ai = admitted_source(pool, db)
    job = PostgresRemoteProcessingIntake(pool).admit(owner, intent, new_id(), storage, ai, NOTICE_VERSION)
    target = db('SELECT * FROM server_processing_jobs WHERE job_id=%s', (job.job_id,))[0]
    database = db('SELECT current_database() AS d')[0]['d']
    config = VerificationConfig(database, schema, owner, job.job_id, str(target['paper_id']),
        target['server_version_id'], target['payload_hash'], 'restricted-worker', 60, 100_000)
    policy = new_id()
    db("""INSERT INTO server_dispatch_budget_limits(scope_type,scope_id,policy_version,window_key,max_input_tokens,max_output_tokens)
        VALUES ('service','service',%s,'test',1000,1000),('owner',%s,%s,'test',1000,1000)""", (policy, owner, policy))
    observations = []

    def factory(auth, **kwargs):
        def create(source, check):
            def run(current, body):
                # 실서비스 DB 원장과 heartbeat를 그대로 사용하며 외부 처리 결과만 대체한다.
                PostgresProcessingJobStore(runtime).heartbeat(owner, source.job_id, source.lease_token, lease_seconds=60)
                ledger = auth.ledger(source, check, policy_version=policy, window_key='test')
                permit = ledger.authorize(AttemptRequest(auth.dispatch_intent(source), 'one', 'translation', 100, 50))
                assert permit is not None
                if mode == 'unknown':
                    ledger.unknown(permit.attempt_id)
                else:
                    ledger.observe(permit.attempt_id, 5, 2)
                    ledger.observe(permit.attempt_id, 5, 2)
                if mode == 'revoked':
                    db('UPDATE server_remote_consents SET revoked_at=CURRENT_TIMESTAMP WHERE consent_id=%s', (ai,))
                observations.append(source)
                return result(source)
            return NS(run=run)
        return create
    monkeypatch.setattr(composition, 'ProcessingStagesFactory', factory)
    class Objects:
        def read_object(self, key, *, expected_size):
            return DownloadedObject(key, expected_size, 'application/pdf', 'a' * 64, True, b'%PDF-test!')
    resources = NS(pool=runtime, objects=Objects(), stage_options=dict(limits=LIMITS,
        service_config=NS(translation_settings=SETTINGS), embedding_spec=SPEC))
    if mode == 'published':
        assert run_once(config, resources) == 'published'
        assert run_once(config, resources) == 'not_claimed'
        assert db('SELECT status FROM server_processing_jobs WHERE job_id=%s', (job.job_id,))[0]['status'] == 'succeeded'
        assert db('SELECT count(*) AS n FROM server_embedding_vectors WHERE owner_user_id=%s', (owner,))[0]['n'] == 1
        assert db('SELECT active_publication_id FROM server_processing_versions WHERE owner_user_id=%s', (owner,))[0]['active_publication_id'] is not None
    else:
        from solo_leveling.workers.remote_processing import RemoteProcessingInputRejected
        with pytest.raises(RemoteProcessingInputRejected):
            run_once(config, resources)
        assert db('SELECT active_publication_id FROM server_processing_versions WHERE owner_user_id=%s', (owner,))[0]['active_publication_id'] is None
    assert len(observations) == 1
    assert db('SELECT count(*) AS n FROM server_processing_publications WHERE source_job_id=%s', (job.job_id,))[0]['n'] == int(mode == 'published')
    usage = db('SELECT reserved_input_tokens, consumed_input_tokens, max_input_tokens FROM server_dispatch_budget_limits WHERE policy_version=%s', (policy,))
    assert all(row == dict(reserved_input_tokens=100 if mode == 'unknown' else 0,
                          consumed_input_tokens=0 if mode == 'unknown' else 5, max_input_tokens=1000) for row in usage)


def test_restricted_worker_cannot_change_policy_or_authentication(pool, db, restricted):
    import psycopg
    from psycopg import sql
    runtime, role, schema = restricted
    # 이 검사만 실행해도 모든 잠금 전용 테이블에 행이 있어야 한다.
    owner, intent, storage, ai = admitted_source(pool, db)
    PostgresRemoteProcessingIntake(pool).admit(owner, intent, new_id(), storage, ai, NOTICE_VERSION)
    with runtime.connection() as conn:
        statements = [
            'SELECT * FROM web_sessions', 'SELECT * FROM server_device_credentials',
            'CREATE TABLE forbidden(id int)', 'ALTER TABLE server_processing_jobs ADD COLUMN forbidden int',
            'CREATE SCHEMA forbidden', 'SET session_replication_role=replica',
            'DELETE FROM server_processing_jobs',
            'UPDATE server_dispatch_budget_limits SET max_input_tokens=999999',
            "UPDATE server_processing_jobs SET owner_user_id='other'",
        ]
        for table, column in LOCK_ONLY.items():
            statements.append(f'UPDATE {table} SET {column}={column}')
        for statement in statements:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute(statement)
        assert conn.execute('SELECT current_user AS u').fetchone()['u'] == role
        assert conn.execute('SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone() == dict(rolsuper=False, rolcreatedb=False, rolcreaterole=False, rolbypassrls=False)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute(sql.SQL('ALTER TABLE {}.server_remote_consents DISABLE TRIGGER ALL').format(sql.Identifier(schema)))


from tests.unit.test_processing_embedder import fixture as embedding_fixture  # noqa: E402,F401


def test_product_composition_with_real_pdf_and_restricted_database(pool, db, restricted, embedding_fixture, monkeypatch):
    from hashlib import sha256
    from unittest.mock import Mock
    import requests
    from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationConfig
    from solo_leveling.application.translation.retry import TranslationRetryPolicy
    from tests.fixtures.pdf_builder import build_minimal_pdf
    from tests.unit.test_remote_processing_stages import envelope
    adapter, spec, _, model, model_factory = embedding_fixture
    runtime, role, schema = restricted
    pdf = build_minimal_pdf(['The first synthetic page has a relay.', 'The second page has another depot.'])
    owner, intent, storage, ai = admitted_source(pool, db, pdf=pdf)
    job = PostgresRemoteProcessingIntake(pool).admit(owner, intent, new_id(), storage, ai, NOTICE_VERSION)
    target = db('SELECT * FROM server_processing_jobs WHERE job_id=%s', (job.job_id,))[0]
    database = db('SELECT current_database() AS d')[0]['d']
    config = VerificationConfig(database, schema, owner, job.job_id, str(target['paper_id']),
        target['server_version_id'], target['payload_hash'], 'restricted-worker', 60, 100_000)
    policy = new_id()
    db("""INSERT INTO server_dispatch_budget_limits(scope_type,scope_id,policy_version,window_key,max_input_tokens,max_output_tokens)
        VALUES ('service','service',%s,'test',100000,100000),('owner',%s,%s,'test',100000,100000)""", (policy, owner, policy))
    def forbidden(*args, **kwargs):
        pytest.fail('외부 HTTP 호출 금지')
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    response = Mock(status_code=200)
    response.json.return_value = envelope('합성 중계는 거점을 연결합니다.')
    post = Mock(return_value=response)
    class Objects:
        def read_object(self, key, *, expected_size):
            return DownloadedObject(key, expected_size, 'application/pdf', sha256(pdf).hexdigest(), True, pdf)
    resources = NS(pool=runtime, objects=Objects(), stage_options=dict(limits=LIMITS,
        service_config=OpenAITranslationConfig('synthetic-service-secret', allow_external_api=True),
        model_dir=adapter.model_dir, embedding_spec=spec, retry_policy=TranslationRetryPolicy(
            base_delay_seconds=0, jitter_seconds=0), policy_version=policy, window_key='test',
        token_upper_bound=lambda *_: 1000, batch_size=8, post=post, model_factory=model_factory))
    assert run_once(config, resources) == 'published'
    assert run_once(config, resources) == 'not_claimed'
    assert post.call_count == 2
    model_factory.assert_called_once()
    assert db('SELECT count(*) AS n FROM server_embedding_vectors WHERE owner_user_id=%s', (owner,))[0]['n'] == 2
    assert db('SELECT count(*) AS n FROM server_processing_result_staging WHERE owner_user_id=%s', (owner,))[0]['n'] == 1
    assert db("SELECT count(*) AS n FROM server_processing_provider_attempts WHERE owner_user_id=%s AND status='observed'", (owner,))[0]['n'] == 2
    assert db('SELECT active_publication_id FROM server_processing_versions WHERE owner_user_id=%s', (owner,))[0]['active_publication_id'] is not None
