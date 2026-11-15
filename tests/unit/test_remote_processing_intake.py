from contextlib import contextmanager

import pytest

from solo_leveling.interfaces.http.remote_processing_intake import (
    PostgresRemoteProcessingIntake,
    RemoteProcessingConflict,
)


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
        for marker, values in self.routes.items():
            if marker in sql:
                value = values.pop(0)
                return Result(value if isinstance(value, list) else (() if value is None else (value,)))
        return Result()


class Pool:
    def __init__(self, routes):
        self.conn = Conn(routes)

    @contextmanager
    def connection(self):
        yield self.conn


SOURCE = {"intent_id": "intent", "paper_id": "paper", "server_version_id": "version", "payload_hash": "a" * 64}
JOB = {"job_id": "job", "paper_id": "paper", "server_version_id": "version", "status": "queued"}
CONSENTS = [
    {"consent_id": "storage", "purpose": "storage", "input_hash": "a" * 64},
    {"consent_id": "ai", "purpose": "ai_provider", "input_hash": "a" * 64},
]


def args():
    return ("owner", "intent", "process-key", "storage", "ai", "notice")


def test_verified_upload_and_current_consents_create_one_authorized_job():
    pool = Pool(
        {
            "SELECT j.job_id": [None],
            "SELECT i.intent_id": [SOURCE],
            "SELECT consent_id": [CONSENTS],
            "INSERT INTO server_processing_jobs": [JOB],
        }
    )

    result = PostgresRemoteProcessingIntake(pool).admit(*args())

    assert result.job_id == "job" and result.status == "queued"
    statements = [sql for sql, _ in pool.conn.calls]
    assert "pg_advisory_xact_lock" in statements[0]
    assert "i.status='verified'" in statements[2] and "u.status='ready'" in statements[2]
    assert "p.deleted_at IS NULL" in statements[2]
    assert statements[-2].startswith("INSERT INTO server_processing_jobs")
    assert statements[-1].startswith("INSERT INTO server_processing_job_authorizations")


def test_same_request_replays_without_rechecking_or_consuming_consent():
    existing = {
        **JOB,
        "payload_hash": "a" * 64,
        "upload_intent_id": "intent",
        "storage_consent_id": "storage",
        "ai_consent_id": "ai",
        "notice_version": "notice",
    }
    pool = Pool({"SELECT j.job_id": [existing]})

    result = PostgresRemoteProcessingIntake(pool).admit(*args())

    assert result.job_id == "job"
    assert len(pool.conn.calls) == 2


def test_same_request_with_different_authorization_conflicts():
    existing = {
        **JOB,
        "payload_hash": "a" * 64,
        "upload_intent_id": "intent",
        "storage_consent_id": "other",
        "ai_consent_id": "ai",
        "notice_version": "notice",
    }
    pool = Pool({"SELECT j.job_id": [existing]})

    with pytest.raises(RemoteProcessingConflict):
        PostgresRemoteProcessingIntake(pool).admit(*args())


@pytest.mark.parametrize("source,consents", [(None, CONSENTS), (SOURCE, CONSENTS[:1])])
def test_unverified_missing_or_incomplete_consent_never_queues(source, consents):
    pool = Pool({"SELECT j.job_id": [None], "SELECT i.intent_id": [source], "SELECT consent_id": [consents]})

    assert PostgresRemoteProcessingIntake(pool).admit(*args()) is None

    assert not any("INSERT INTO server_processing_jobs" in sql for sql, _ in pool.conn.calls)


def test_invalid_input_fails_before_database():
    pool = Pool({})

    with pytest.raises(ValueError, match="remote_processing_input_invalid"):
        PostgresRemoteProcessingIntake(pool).admit("", *args()[1:])

    assert pool.conn.calls == []
