from contextlib import contextmanager

import pytest

from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return Result(self.row)


class Pool:
    def __init__(self, row):
        self.conn = Connection(row)

    @contextmanager
    def connection(self):
        yield self.conn


def row(**changes):
    value = {
        "owner_user_id": "owner-1",
        "context_id": "ctx-old",
        "paper_id": "paper-1",
        "server_version_id": "version-1",
        "publication_id": "publication-old",
        "parse_revision_id": "parse-old",
        "translation_revision_id": "translation-old",
        "embedding_set_id": "embedding-old",
        "publication_policy": "partial",
        "model_fingerprint": {"runtime": "onnx", "dimension": 384},
    }
    value.update(changes)
    return value


def test_resolves_owner_scoped_context_without_following_active_pointer():
    pool = Pool(row())
    binding = PostgresRemoteContextResolver(pool).resolve("owner-1", "ctx-old")

    assert binding.owner_user_id == "owner-1"
    assert binding.publication_id == "publication-old"
    assert binding.embedding_set_id == "embedding-old"
    assert binding.model_fingerprint == '{"dimension":384,"runtime":"onnx"}'
    sql, params = pool.conn.calls[0]
    assert params == ("owner-1", "ctx-old")
    assert "active_publication_id" not in sql
    assert "c.owner_user_id=%s AND c.context_id=%s" in sql
    assert "e.status='ready'" in sql


def test_other_owner_and_missing_context_are_indistinguishable():
    pool = Pool(None)
    assert PostgresRemoteContextResolver(pool).resolve("owner-2", "ctx-old") is None


@pytest.mark.parametrize("owner,context", [("", "ctx"), ("owner", " "), (None, "ctx")])
def test_invalid_lookup_is_rejected_before_query(owner, context):
    pool = Pool(None)
    with pytest.raises(ValueError, match="^remote_context_lookup_invalid$"):
        PostgresRemoteContextResolver(pool).resolve(owner, context)
    assert pool.conn.calls == []


@pytest.mark.parametrize("fingerprint", [{}, [], "private", None])
def test_non_object_or_empty_model_fingerprint_fails_closed(fingerprint):
    with pytest.raises(ValueError, match="^remote_model_fingerprint_invalid$"):
        PostgresRemoteContextResolver(Pool(row(model_fingerprint=fingerprint))).resolve("owner-1", "ctx-old")
