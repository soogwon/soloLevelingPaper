"""전송 원장의 재시작 방어를 일회용 PostgreSQL 스키마에서 검증한다."""

import os
from pathlib import Path
from uuid import uuid4

import pytest

from solo_leveling.application.provider_dispatch import AttemptRequest, DispatchIntent
from solo_leveling.interfaces.http.dispatch_ledger import PostgresDispatchLedger


DSN = os.environ.get("SOLO_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="SOLO_TEST_POSTGRES_DSN 필요")


@pytest.fixture
def ledger_pool():
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    schema = f"dispatch_test_{uuid4().hex}"
    migration = Path(__file__).resolve().parents[2] / "migrations/server/0024_remote_dispatch_ledger.sql"
    # #450 단독 계약을 검사하므로 후속 실행 저장소 대신 최소 사용자 참조만 준비한다.
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f'SET search_path TO "{schema}"')
        conn.execute("CREATE TABLE server_users (owner_user_id text PRIMARY KEY)")
        conn.execute(migration.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO server_users VALUES ('owner')")
        conn.execute("""INSERT INTO server_remote_consents
            (consent_id,owner_user_id,purpose,input_hash,notice_version) VALUES
            ('storage','owner','storage','paper','notice'),
            ('ai','owner','ai_provider','input','notice')""")
        conn.execute("""INSERT INTO server_dispatch_controls
            (control_id,generation,dispatch_enabled) VALUES ('provider_dispatch',1,true)""")
        conn.execute("""INSERT INTO server_dispatch_budget_limits
            (scope_type,scope_id,policy_version,window_key,max_input_tokens,max_output_tokens)
            VALUES ('service','service','policy','window',10000,10000),
                   ('owner','owner','policy','window',10000,10000)""")
    pool = ConnectionPool(DSN, kwargs={"row_factory": dict_row, "options": f"-c search_path={schema}"})
    try:
        yield pool
    finally:
        pool.close()
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def new_ledger(pool):
    return PostgresDispatchLedger(pool, "policy", "window", lambda *_: True)


def request(key, lease="lease"):
    intent = DispatchIntent("owner", "execution", lease, "input", "storage", "ai", "notice")
    return AttemptRequest(intent, key, "generation", 100, 50)


@pytest.mark.parametrize("state", ["reserved", "unknown", "overrun"])
def test_restart_and_new_key_cannot_bypass_unsettled_or_overrun_usage(ledger_pool, state):
    ledger = new_ledger(ledger_pool)
    first = ledger.authorize(request("first"))
    assert first is not None
    if state == "unknown":
        ledger.unknown(first.attempt_id)
    elif state == "overrun":
        ledger.observe(first.attempt_id, 101, 50)

    assert new_ledger(ledger_pool).authorize(request("second", "new-lease")) is None
    with ledger_pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM server_provider_attempts").fetchone()["n"] == 1

    if state != "overrun":
        ledger.observe(first.attempt_id, 80, 40)
        assert new_ledger(ledger_pool).authorize(request("second", "new-lease")) is not None


def test_concurrent_reservations_allow_only_one_unsettled_attempt(ledger_pool):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as workers:
        permits = list(workers.map(lambda key: new_ledger(ledger_pool).authorize(request(key)), ["one", "two"]))
    assert sum(permit is not None for permit in permits) == 1
