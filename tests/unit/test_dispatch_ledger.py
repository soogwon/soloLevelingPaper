from contextlib import contextmanager

import pytest

from solo_leveling.application.provider_dispatch import AttemptRequest, DispatchIntent
from solo_leveling.interfaces.http.dispatch_ledger import PostgresDispatchLedger


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class Conn:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        for marker, results in self.routes.items():
            if marker in sql:
                value = results.pop(0) if isinstance(results, list) else results
                return Result(value if isinstance(value, list) else (() if value is None else (value,)))
        return Result()


class Pool:
    def __init__(self, routes):
        self.conn = Conn(routes)

    @contextmanager
    def connection(self):
        yield self.conn


def request():
    intent = DispatchIntent("owner", "execution", "lease", "input-hash", "storage", "ai", "notice-v1")
    return AttemptRequest(intent, "stable-key", "generation", 100, 50)


def limits(**changes):
    base = {
        "max_input_tokens": 1000,
        "max_output_tokens": 1000,
        "reserved_input_tokens": 0,
        "reserved_output_tokens": 0,
        "consumed_input_tokens": 0,
        "consumed_output_tokens": 0,
    }
    return [{"scope_type": scope, "scope_id": scope, **base, **changes} for scope in ("owner", "service")]


def authorization_routes(**changes):
    routes = {
        "FROM server_dispatch_controls": [{"generation": 3, "dispatch_enabled": True}],
        "FROM server_remote_consents": [
            [
                {"consent_id": "storage", "purpose": "storage", "input_hash": "paper-hash"},
                {"consent_id": "ai", "purpose": "ai_provider", "input_hash": "input-hash"},
            ]
        ],
        "FROM server_dispatch_budget_limits": [limits()],
        "INSERT INTO server_provider_attempts": [{"attempt_id": "attempt"}],
    }
    routes.update(changes)
    return routes


def test_authorize_locks_scope_consent_control_and_both_limits_before_reserving():
    pool = Pool(authorization_routes())
    seen = []
    ledger = PostgresDispatchLedger(pool, "policy-v1", "2026-11", lambda conn, intent: seen.append(intent) or True)

    permit = ledger.authorize(request())

    assert permit is not None and permit.request == request()
    assert seen == [request().intent]
    sql = "\n".join(call[0] for call in pool.conn.calls)
    assert "FOR UPDATE" in sql and "FOR SHARE" in sql
    assert "scope_type='service'" in sql and "scope_type='owner'" in sql
    assert "reserved_input_tokens=reserved_input_tokens+%s" in sql


@pytest.mark.parametrize(
    "routes",
    [
        authorization_routes(**{"FROM server_dispatch_controls": [{"generation": 3, "dispatch_enabled": False}]}),
        authorization_routes(
            **{
                "FROM server_remote_consents": [
                    [{"consent_id": "storage", "purpose": "storage", "input_hash": "paper"}]
                ]
            }
        ),
        authorization_routes(**{"FROM server_dispatch_budget_limits": [limits(consumed_input_tokens=950)]}),
        authorization_routes(**{"INSERT INTO server_provider_attempts": [None]}),
    ],
)
def test_denial_never_increments_budget(routes):
    pool = Pool(routes)
    ledger = PostgresDispatchLedger(pool, "policy-v1", "2026-11", lambda *_: True)

    assert ledger.authorize(request()) is None
    assert not any("reserved_input_tokens=reserved_input_tokens+%s" in sql for sql, _ in pool.conn.calls)


def test_scope_denial_happens_before_policy_or_consent_reads():
    pool = Pool(authorization_routes())
    ledger = PostgresDispatchLedger(pool, "policy-v1", "2026-11", lambda *_: False)

    assert ledger.authorize(request()) is None
    assert pool.conn.calls == []


def test_new_ledger_cannot_bypass_unsettled_or_overrun_attempt():
    pool = Pool(authorization_routes(**{"FROM server_provider_attempts": [{"blocked": 1}]}))
    ledger = PostgresDispatchLedger(pool, "new-policy", "new-window", lambda *_: True)
    original = request()
    retry = AttemptRequest(original.intent, "new-key", "repair", 100, 50)

    assert ledger.authorize(retry) is None
    assert not any("INSERT INTO server_provider_attempts" in sql for sql, _ in pool.conn.calls)


def test_observe_is_idempotent_and_conflicting_replay_is_rejected():
    observed = {
        "status": "observed",
        "owner_user_id": "owner",
        "policy_version": "policy-v1",
        "window_key": "2026-11",
        "reserved_input_tokens": 100,
        "reserved_output_tokens": 50,
        "actual_input_tokens": 80,
        "actual_output_tokens": 40,
    }
    pool = Pool({"FROM server_provider_attempts": [observed, observed]})
    ledger = PostgresDispatchLedger(pool, "policy-v1", "2026-11", lambda *_: True)

    ledger.observe("attempt", 80, 40)
    with pytest.raises(ValueError, match="dispatch_usage_conflict"):
        ledger.observe("attempt", 81, 40)
    assert not any("UPDATE server_dispatch_budget_limits" in sql for sql, _ in pool.conn.calls)


def test_observe_releases_reservation_and_records_even_overrun():
    reserved = {
        "status": "reserved",
        "owner_user_id": "owner",
        "policy_version": "policy-v1",
        "window_key": "2026-11",
        "reserved_input_tokens": 100,
        "reserved_output_tokens": 50,
        "actual_input_tokens": None,
        "actual_output_tokens": None,
    }
    pool = Pool({"FROM server_provider_attempts": [reserved]})
    ledger = PostgresDispatchLedger(pool, "policy-v1", "2026-11", lambda *_: True)

    ledger.observe("attempt", 120, 60)

    settle = next((sql, params) for sql, params in pool.conn.calls if "UPDATE server_dispatch_budget_limits" in sql)
    assert settle[1][:4] == (100, 50, 120, 60)


def test_unknown_is_sticky_and_does_not_release_reservation():
    reserved = {
        "status": "reserved",
        "owner_user_id": "owner",
        "policy_version": "policy-v1",
        "window_key": "2026-11",
        "reserved_input_tokens": 100,
        "reserved_output_tokens": 50,
        "actual_input_tokens": None,
        "actual_output_tokens": None,
    }
    pool = Pool({"FROM server_provider_attempts": [reserved]})
    ledger = PostgresDispatchLedger(pool, "policy-v1", "2026-11", lambda *_: True)

    ledger.unknown("attempt")

    assert any("status='unknown'" in sql for sql, _ in pool.conn.calls)
    assert not any("UPDATE server_dispatch_budget_limits" in sql for sql, _ in pool.conn.calls)


def test_late_usage_reconciles_unknown_reservation():
    unknown = {
        "status": "unknown",
        "owner_user_id": "owner",
        "policy_version": "policy-v1",
        "window_key": "2026-11",
        "reserved_input_tokens": 100,
        "reserved_output_tokens": 50,
        "actual_input_tokens": None,
        "actual_output_tokens": None,
    }
    pool = Pool({"FROM server_provider_attempts": [unknown]})
    ledger = PostgresDispatchLedger(pool, "policy-v1", "2026-11", lambda *_: True)

    ledger.observe("attempt", 90, 45)

    assert any("status IN ('reserved','unknown')" in sql for sql, _ in pool.conn.calls)
    settle = next((sql, params) for sql, params in pool.conn.calls if "UPDATE server_dispatch_budget_limits" in sql)
    assert settle[1][:4] == (100, 50, 90, 45)


@pytest.mark.parametrize("usage", [(True, 1), (-1, 1), (1, -1)])
def test_invalid_usage_is_rejected_before_database(usage):
    pool = Pool({})
    ledger = PostgresDispatchLedger(pool, "policy-v1", "2026-11", lambda *_: True)

    with pytest.raises(ValueError, match="dispatch_usage_invalid"):
        ledger.observe("attempt", *usage)

    assert pool.conn.calls == []
