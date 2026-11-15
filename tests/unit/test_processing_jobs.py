from contextlib import nullcontext

import pytest

from solo_leveling.interfaces.http.processing_jobs import (
    PostgresProcessingJobStore,
    ProcessingJobConflict,
    ProcessingJobLeaseLost,
    ProcessingJobNotFound,
)


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return Result(self.rows.pop(0) if self.rows else None)


class Pool:
    def __init__(self, conn):
        self.conn = conn

    def connection(self):
        return nullcontext(self.conn)


def job_row(**changes):
    row = {
        "job_id": "job",
        "owner_user_id": "user-a",
        "paper_id": "paper",
        "server_version_id": "v1",
        "job_kind": "full_processing",
        "request_key": "request",
        "payload_hash": "a" * 64,
        "status": "queued",
        "attempt": 0,
        "max_attempts": 3,
        "retryable": True,
        "lease_token": None,
    }
    row.update(changes)
    return row


def test_enqueue_is_owner_scoped_and_returns_existing_same_payload():
    existing = job_row()
    conn = Connection((None, existing))
    store = PostgresProcessingJobStore(Pool(conn))

    result = store.enqueue(
        "user-a", "paper", "v1", "full_processing", "request", "a" * 64
    )

    assert result.job_id == "job"
    assert conn.calls[0][1][-3:] == ("user-a", "paper", "v1")
    assert conn.calls[1][1] == ("user-a", "request")


def test_enqueue_rejects_same_key_with_different_payload():
    conn = Connection((None, job_row(payload_hash="b" * 64)))

    with pytest.raises(ProcessingJobConflict, match="payload_conflict"):
        PostgresProcessingJobStore(Pool(conn)).enqueue(
            "user-a", "paper", "v1", "full_processing", "request", "a" * 64
        )


def test_enqueue_hides_missing_or_other_owner_version_as_not_found():
    conn = Connection((None, None))

    with pytest.raises(ProcessingJobNotFound, match="not_found"):
        PostgresProcessingJobStore(Pool(conn)).enqueue(
            "user-b", "paper", "v1", "full_processing", "request", "a" * 64
        )


def test_claim_uses_skip_locked_and_rotates_execution_token():
    claimed = job_row(status="running", attempt=1, lease_token="new-token")
    conn = Connection((claimed,))

    result = PostgresProcessingJobStore(Pool(conn)).claim("worker-1", lease_seconds=60)

    assert result is not None
    assert result.lease_token == "new-token"
    sql, params = conn.calls[0]
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "attempt < max_attempts" in sql
    assert "max_attempts_exhausted" in sql
    assert "lease_expired_not_retryable" in sql
    assert params[0] == "worker-1"
    assert params[2] == 60


@pytest.mark.parametrize("method", ["heartbeat", "succeed"])
def test_expired_or_replaced_lease_rejects_late_worker(method):
    store = PostgresProcessingJobStore(Pool(Connection((None,))))

    with pytest.raises(ProcessingJobLeaseLost, match="lease_lost"):
        getattr(store, method)("user-a", "job", "old-token")


def test_retryable_failure_requeues_without_finished_timestamp():
    conn = Connection(({"job_id": "job"},))

    PostgresProcessingJobStore(Pool(conn)).fail(
        "user-a", "job", "lease", error_code="provider_timeout", retryable=True
    )

    sql, params = conn.calls[0]
    assert "attempt < max_attempts THEN 'queued'" in sql
    assert params[-3:] == ("user-a", "job", "lease")


def test_cancel_clears_lease_and_is_owner_scoped():
    conn = Connection(({"job_id": "job"},))

    PostgresProcessingJobStore(Pool(conn)).cancel("user-a", "job")

    sql, params = conn.calls[0]
    assert "lease_token=NULL" in sql
    assert params[-2:] == ("user-a", "job")


def test_claim_fails_expired_non_retryable_jobs_instead_of_leaving_them_running():
    conn = Connection((None,))

    assert PostgresProcessingJobStore(Pool(conn)).claim("worker-1") is None

    sql = conn.calls[0][0]
    assert "attempt >= max_attempts OR retryable=false" in sql


@pytest.mark.parametrize("method", ["claim", "heartbeat", "succeed", "fail", "cancel"])
def test_lease_decisions_use_the_database_clock_only(method):
    conn = Connection(({"job_id": "job", **job_row(status="running", lease_token="t")},))
    store = PostgresProcessingJobStore(Pool(conn))

    if method == "claim":
        store.claim("worker-1")
    elif method == "fail":
        store.fail("user-a", "job", "t", error_code="provider_timeout", retryable=False)
    elif method == "cancel":
        store.cancel("user-a", "job")
    else:
        getattr(store, method)("user-a", "job", "t")

    sql, params = conn.calls[0]
    assert "CURRENT_TIMESTAMP" in sql
    # 앱 서버 시각을 인자로 넘기지 않는다. 서버 사이의 시계 차이로 lease 판정이 갈리면 안 된다.
    assert not any(hasattr(value, "tzinfo") for value in params)


def test_heartbeat_rejects_non_positive_lease():
    with pytest.raises(ValueError, match="lease_seconds_invalid"):
        PostgresProcessingJobStore(Pool(Connection())).heartbeat(
            "user-a", "job", "t", lease_seconds=0
        )
