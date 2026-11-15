from contextlib import contextmanager

import pytest

from solo_leveling.infrastructure.storage.remote_deletion_backend import (
    PostgresObjectDeletionBackend,
    create_remote_deletion_cleanup,
)


class Result:
    def __init__(self, one=None, rows=()):
        self.one, self.rows = one, rows

    def fetchone(self):
        return self.one

    def fetchall(self):
        return list(self.rows)


class Connection:
    def __init__(self, keys=()):
        self.keys = keys
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "SELECT paper_id FROM server_papers" in sql:
            return Result({"paper_id": "paper"})
        if "SELECT object_key FROM server_pdf_uploads" in sql:
            return Result(rows=[{"object_key": key} for key in self.keys])
        return Result()


class Pool:
    def __init__(self, keys=()):
        self.connections = []
        self.keys = keys

    @contextmanager
    def connection(self):
        conn = Connection(self.keys)
        self.connections.append(conn)
        yield conn


class Objects:
    def __init__(self):
        self.deleted = []

    def delete_upload(self, key):
        self.deleted.append(key)


def all_calls(pool):
    return [call for connection in pool.connections for call in connection.calls]


def test_derived_cleanup_is_owner_scoped_and_preserves_parent_tombstone():
    pool = Pool()
    PostgresObjectDeletionBackend(pool, Objects()).delete_derived("owner", "paper")
    calls = all_calls(pool)
    mutation = [(sql, params) for sql, params in calls if sql.lstrip().startswith(("DELETE", "UPDATE"))]
    assert mutation
    assert all(params == ("owner", "paper") for _, params in mutation)
    assert not any("DELETE FROM server_papers" in sql for sql, _ in mutation)
    assert not any("server_deletion_requests" in sql for sql, _ in mutation)
    statements = [sql.lstrip() for sql, _ in mutation]
    def position(prefix):
        return next(index for index, sql in enumerate(statements) if sql.startswith(prefix))
    assert position("DELETE FROM server_provider_attempts") < position("DELETE FROM server_remote_executions")
    assert position("DELETE FROM server_processing_evidences") < position("DELETE FROM server_execution_contexts")
    assert position("UPDATE server_processing_versions") < position("DELETE FROM server_processing_publications")


def test_source_cleanup_deletes_private_objects_before_database_rows():
    pool, objects = Pool(("private/tmp/a.pdf", "private/files/b.pdf")), Objects()
    result = PostgresObjectDeletionBackend(pool, objects).delete_source("owner", "paper")
    assert result.deleted_objects == 2
    assert objects.deleted == ["private/tmp/a.pdf", "private/files/b.pdf"]
    mutations = [sql for sql, _ in all_calls(pool) if sql.lstrip().startswith("DELETE")]
    assert [next(name for name in (
        "server_remote_upload_intents", "server_pdf_uploads", "server_processing_versions") if name in sql)
        for sql in mutations] == [
            "server_remote_upload_intents", "server_pdf_uploads", "server_processing_versions"]


@pytest.mark.parametrize("key", ["public/a.pdf", "../private/a.pdf", "private/../a.pdf", "private\\a.pdf", "private/a\x00.pdf"])
def test_source_cleanup_rejects_unsafe_key_before_object_deletion(key):
    pool, objects = Pool((key,)), Objects()
    with pytest.raises(ValueError, match="remote_deletion_object_key_invalid"):
        PostgresObjectDeletionBackend(pool, objects).delete_source("owner", "paper")
    assert objects.deleted == []
    assert not any(sql.lstrip().startswith("DELETE") for sql, _ in all_calls(pool))


def test_factory_uses_approved_policy_and_concrete_backend():
    service = create_remote_deletion_cleanup(
        Pool(), Objects(), worker_id="worker", lease_seconds=60)
    assert service.policy.tombstone_retention_days == 60
    assert service.policy.backup_expiry_days == 30
    assert service.policy.account_erasure_days == 30
    assert isinstance(service.backend, PostgresObjectDeletionBackend)
