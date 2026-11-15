from contextlib import contextmanager
from dataclasses import replace

import pytest

from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchBinding, RemoteSearchFilters
from solo_leveling.interfaces.http.remote_vectors import (
    EmbeddingWriteScope,
    PostgresEmbeddingVectorWriter,
    PostgresRemoteScopedSearch,
)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Connection:
    def __init__(self, rows=()):
        self.rows = rows
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return Result(self.rows)


class Pool:
    def __init__(self, rows=()):
        self.conn = Connection(rows)

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def binding():
    return RemoteSearchBinding(
        "owner", "context", "paper", "version", "publication", "parse", "translation", "set",
        '{"dimension":3,"distance":"cosine","normalize":true}', "complete",
    )


def test_search_applies_all_scope_filters_before_limit(binding):
    pool = Pool([{"chunk_id": "c1", "pdf_page": 6, "section_id": "method", "cosine_distance": 0.25}])
    search = PostgresRemoteScopedSearch(pool, lambda question, scope: [0.1, 0.2, 0.3])

    hits = search.search("질문", binding, RemoteSearchFilters((6,), ("method",)), 2)

    assert [(hit.chunk_id, hit.pdf_page, hit.section_id) for hit in hits] == [("c1", 6, "method")]
    sql, params = pool.conn.calls[0]
    assert sql.index("v.owner_user_id=%s") < sql.index("ORDER BY") < sql.index("LIMIT")
    assert "c.pdf_page = ANY" in sql and "c.section_id = ANY" in sql
    assert params[1:7] == ("owner", "paper", "version", "parse", "translation", "set")
    assert params[7:11] == ([6], [6], ["method"], ["method"])


@pytest.mark.parametrize("vector", [[1, 2], [1, 2, float("nan")], [1, 2, True], "123"])
def test_search_rejects_wrong_or_non_finite_query_vector(binding, vector):
    pool = Pool()
    with pytest.raises(ValueError, match="^remote_embedding_invalid$"):
        PostgresRemoteScopedSearch(pool, lambda *_: vector).search(
            "question", binding, RemoteSearchFilters(), 5
        )
    assert pool.conn.calls == []


def test_search_rejects_invalid_fingerprint_before_query(binding):
    with pytest.raises(ValueError, match="^remote_model_fingerprint_invalid$"):
        PostgresRemoteScopedSearch(Pool(), lambda *_: [1]).search(
            "question", replace(binding, model_fingerprint="{}"), RemoteSearchFilters(), 5
        )


def test_writer_requires_building_set_matching_dimension_and_chunk(binding):
    pool = Pool([{"chunk_id": "c1"}])
    scope = EmbeddingWriteScope("owner", "paper", "version", "parse", "translation", "set", 3)
    PostgresEmbeddingVectorWriter(pool).put(scope, "c1", [0.1, 0.2, 0.3])

    sql, params = pool.conn.calls[0]
    assert "e.status='building'" in sql
    assert "t.chunk_id=%s" in sql
    assert "model_fingerprint->>'dimension'" in sql
    assert params[-2:] == ("c1", 3)


def test_writer_fails_when_target_is_not_in_building_set(binding):
    scope = EmbeddingWriteScope("owner", "paper", "version", "parse", "translation", "set", 3)
    with pytest.raises(ValueError, match="^remote_embedding_target_invalid$"):
        PostgresEmbeddingVectorWriter(Pool()).put(scope, "c1", [0.1, 0.2, 0.3])
