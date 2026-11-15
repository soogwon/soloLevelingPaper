from contextlib import contextmanager

import pytest

from solo_leveling.interfaces.http.remote_deletions import PostgresRemoteDeletionStore


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class Conn:
    def __init__(self, old=None, paper=None, created=None):
        self.old = old
        self.paper = paper
        self.created = created
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "SELECT request_id,paper_id,status" in sql:
            return Result(self.old)
        if "SELECT paper_id FROM server_papers" in sql:
            return Result(self.paper)
        if "INSERT INTO server_deletion_requests" in sql:
            return Result(self.created)
        return Result()


class Pool:
    def __init__(self, **kwargs):
        self.conn = Conn(**kwargs)

    @contextmanager
    def connection(self):
        yield self.conn


def test_replays_same_request_without_mutating_paper_or_jobs():
    old = {"request_id": "r", "paper_id": "p", "status": "blocking"}
    pool = Pool(old=old)

    result = PostgresRemoteDeletionStore(pool).request("owner", "p", "key")

    assert (result.request_id, result.paper_id, result.status) == ("r", "p", "blocking")
    sql = "\n".join(call[0] for call in pool.conn.calls)
    assert "UPDATE server_papers" not in sql
    assert "UPDATE server_processing_jobs" not in sql


def test_replays_existing_paper_deletion_with_a_new_request_key():
    old = {"request_id": "r", "paper_id": "p", "status": "blocking"}
    pool = Pool(old=old)

    result = PostgresRemoteDeletionStore(pool).request("owner", "p", "another-key")

    assert result.request_id == "r"
    lookup_params = next(params for sql, params in pool.conn.calls if "SELECT request_id,paper_id,status" in sql)
    assert lookup_params == ("owner", "another-key", "p")


def test_rejects_request_key_reuse_for_another_paper():
    pool = Pool(old={"request_id": "r", "paper_id": "other", "status": "blocking"})

    with pytest.raises(ValueError, match="remote_deletion_request_conflict"):
        PostgresRemoteDeletionStore(pool).request("owner", "p", "key")


def test_missing_owner_scoped_paper_returns_none_without_mutation():
    pool = Pool()

    assert PostgresRemoteDeletionStore(pool).request("owner", "p", "key") is None
    sql = "\n".join(call[0] for call in pool.conn.calls)
    assert "UPDATE server_processing_jobs" not in sql
    assert "UPDATE server_papers" not in sql


def test_cancels_jobs_before_tombstoning_and_records_request():
    created = {"request_id": "r", "paper_id": "p", "status": "blocking"}
    pool = Pool(paper={"paper_id": "p"}, created=created)

    result = PostgresRemoteDeletionStore(pool).request("owner", "p", "key")

    assert result.status == "blocking"
    statements = [sql for sql, _ in pool.conn.calls]
    cancel_index = next(i for i, sql in enumerate(statements) if "UPDATE server_processing_jobs" in sql)
    tombstone_index = next(i for i, sql in enumerate(statements) if "UPDATE server_papers" in sql)
    assert cancel_index < tombstone_index
    cancel_sql = statements[cancel_index]
    assert "status='cancelled'" in cancel_sql
    assert "lease_owner=NULL" in cancel_sql
    assert "status IN ('queued','running')" in cancel_sql


@pytest.mark.parametrize("values", [("", "p", "key"), ("owner", " ", "key"), ("owner", "p", "")])
def test_rejects_invalid_input_before_database(values):
    pool = Pool()

    with pytest.raises(ValueError, match="remote_deletion_input_invalid"):
        PostgresRemoteDeletionStore(pool).request(*values)

    assert pool.conn.calls == []
