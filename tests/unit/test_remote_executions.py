from contextlib import contextmanager

import pytest

from solo_leveling.application.provider_dispatch import DispatchIntent
from solo_leveling.application.remote_replay import ExecutionLookup
from solo_leveling.interfaces.http.remote_executions import (
    PostgresRemoteExecutionStore,
    RemoteExecutionConflict,
    RemoteExecutionLeaseLost,
    validate_execution_scope,
)


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Conn:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        for marker, values in self.routes.items():
            if marker in sql:
                value = values.pop(0)
                return Result(() if value is None else (value,))
        return Result()


class Pool:
    def __init__(self, routes):
        self.conn = Conn(routes)

    @contextmanager
    def connection(self):
        yield self.conn


LOOKUP = ExecutionLookup("owner", "ask_paper", "key", "hash", "context")


def row(**changes):
    value = {
        "execution_id": "execution",
        "owner_user_id": "owner",
        "operation": "ask_paper",
        "request_key": "key",
        "input_hash": "hash",
        "context_id": "context",
        "storage_consent_id": "storage",
        "ai_consent_id": "ai",
        "notice_version": "notice",
        "state": "queued",
        "budget_state": "available",
        "lease_token": None,
        "payload_json": None,
        "payload_sha256": None,
        "deleted_at": None,
    }
    value.update(changes)
    return value


def test_create_admits_and_inserts_owner_scoped_execution():
    pool = Pool({"SELECT e.*,p.deleted_at": [None], "INSERT INTO server_remote_executions": [row()]})
    admitted = []
    store = PostgresRemoteExecutionStore(pool, lambda conn, *args: admitted.append(args) or True)

    created = store.create(LOOKUP, "storage", "ai", "notice")

    assert created.execution_id == "execution" and created.access_allowed
    assert admitted == [(LOOKUP, "storage", "ai", "notice")]
    assert "pg_advisory_xact_lock" in pool.conn.calls[0][0]
    insert = next((sql, params) for sql, params in pool.conn.calls if "INSERT INTO server_remote_executions" in sql)
    assert "p.deleted_at IS NULL" in insert[0]
    assert insert[1][1:4] == ("ask_paper", "key", "hash")


def test_same_key_replays_and_different_input_conflicts_without_admission():
    existing = row()
    pool = Pool({"SELECT e.*,p.deleted_at": [existing]})
    store = PostgresRemoteExecutionStore(pool, lambda *_: pytest.fail("replay must not admit again"))
    assert store.create(LOOKUP, "storage", "ai", "notice").execution_id == "execution"

    conflict_pool = Pool({"SELECT e.*,p.deleted_at": [existing]})
    with pytest.raises(RemoteExecutionConflict):
        PostgresRemoteExecutionStore(conflict_pool).create(
            ExecutionLookup("owner", "ask_paper", "key", "other", "context"), "storage", "ai", "notice"
        )


def test_missing_or_tombstoned_context_returns_none_without_disclosure():
    pool = Pool({"SELECT e.*,p.deleted_at": [None, None], "INSERT INTO server_remote_executions": [None]})
    assert PostgresRemoteExecutionStore(pool).create(LOOKUP, "storage", "ai", "notice") is None


def test_claim_uses_expiring_generation_and_returns_dispatch_intent():
    claimed = row(state="running", lease_token="lease")
    pool = Pool({"UPDATE server_remote_executions e": [claimed]})

    intent = PostgresRemoteExecutionStore(pool).claim("owner", "execution", lease_seconds=30)

    assert intent == DispatchIntent("owner", "execution", "lease", "hash", "storage", "ai", "notice")
    sql, params = pool.conn.calls[0]
    assert "lease_expires_at<=CURRENT_TIMESTAMP" in sql
    assert params[1] == 30


def test_complete_requires_current_lease_context_and_tombstone_check():
    pool = Pool({"UPDATE server_remote_executions e": [{"execution_id": "execution"}]})
    payload = {"context_id": "context", "status": "insufficient"}

    PostgresRemoteExecutionStore(pool).complete("owner", "execution", "lease", payload)

    sql, params = pool.conn.calls[0]
    assert "lease_expires_at>CURRENT_TIMESTAMP" in sql
    assert "p.deleted_at IS NULL" in sql
    assert params[-1] == "context"


def test_complete_rejects_lost_lease_and_bad_payload_before_write():
    pool = Pool({"UPDATE server_remote_executions e": [None]})
    store = PostgresRemoteExecutionStore(pool)
    with pytest.raises(RemoteExecutionLeaseLost):
        store.complete("owner", "execution", "lease", {"context_id": "context"})
    with pytest.raises(ValueError, match="remote_execution_result_invalid"):
        store.complete("owner", "execution", "lease", {"status": "partial"})


def test_lookup_returns_deleted_execution_as_inaccessible_without_mutation():
    pool = Pool({"SELECT e.*,p.deleted_at": [row(deleted_at="deleted")]})

    stored = PostgresRemoteExecutionStore(pool).lookup("owner", "ask_paper", "key")

    assert stored is not None and not stored.access_allowed
    assert all("UPDATE" not in sql and "INSERT" not in sql for sql, _ in pool.conn.calls)


def test_dispatch_scope_locks_exact_execution_generation_and_paper():
    conn = Conn({"SELECT 1 FROM server_remote_executions": [{"?column?": 1}, None]})
    intent = DispatchIntent("owner", "execution", "lease", "hash", "storage", "ai", "notice")

    assert validate_execution_scope(conn, intent)
    assert not validate_execution_scope(conn, intent)
    sql, params = conn.calls[0]
    assert "FOR UPDATE OF e,p" in sql
    assert "lease_expires_at>CURRENT_TIMESTAMP" in sql
    assert "p.deleted_at IS NULL" in sql
    assert params == ("owner", "execution", "lease", "hash", "storage", "ai", "notice")


def test_cancel_request_revokes_lease_before_final_confirmation():
    pool = Pool({"UPDATE server_remote_executions": [{"state": "cancel_requested"}, {"execution_id": "execution"}]})
    store = PostgresRemoteExecutionStore(pool)

    assert store.request_cancel("owner", "execution") == "cancel_requested"
    assert store.confirm_cancel("owner", "execution")

    request_sql = pool.conn.calls[0][0]
    assert "lease_token=NULL" in request_sql and "lease_expires_at=NULL" in request_sql
    assert "state IN ('queued','running')" in request_sql
    assert "state='cancel_requested'" in pool.conn.calls[1][0]
