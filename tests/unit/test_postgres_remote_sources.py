from contextlib import contextmanager
from dataclasses import replace
import json

import pytest

from solo_leveling.application.evidence_qa.remote_contract import (
    RemoteRetrievalContractError,
    RemoteSearchBinding,
)
from solo_leveling.interfaces.http.remote_sources import PostgresRemoteSourceReader


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class Conn:
    def __init__(self, rows=(), error=None):
        self.rows = rows
        self.error = error
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if self.error:
            raise self.error
        return Result(self.rows)


class Pool:
    def __init__(self, rows=(), error=None):
        self.conn = Conn(rows, error)

    @contextmanager
    def connection(self):
        yield self.conn


BINDING = RemoteSearchBinding(
    "owner", "context", "paper", "version", "publication", "parse", "translation", "set", '{"model":"synthetic"}'
)
ROW = {
    "chunk_id": "chunk",
    "ordinal": 4,
    "pdf_page": 6,
    "section_id": "methods",
    "original_text": "Synthetic source.",
    "translated_text": "합성 번역.",
}


def test_reads_exact_chunk_from_fixed_ready_publication_and_vector_set():
    pool = Pool((ROW,))

    rows = PostgresRemoteSourceReader(pool).read_chunks(BINDING, ("chunk",))

    assert len(rows) == 1 and rows[0].binding == BINDING
    assert rows[0].original_text == "Synthetic source." and rows[0].translation_text == "합성 번역."
    sql, params = pool.conn.calls[0]
    assert "p.deleted_at IS NULL" in sql
    assert "pr.status='ready'" in sql and "tr.status='ready'" in sql and "es.status='ready'" in sql
    assert "EXISTS (SELECT 1 FROM server_embedding_vectors" in sql
    assert "FOR SHARE OF p,pub,pr,tr,es" in sql
    assert params[:9] == (
        "owner",
        "context",
        "paper",
        "version",
        "publication",
        "parse",
        "translation",
        "set",
        "complete",
    )
    assert params[-1] == ["chunk"]


def test_following_chunks_use_same_scope_and_only_requested_ordinals():
    pool = Pool((ROW,))

    rows = PostgresRemoteSourceReader(pool).following_chunks(BINDING, (4, 5))

    assert rows[0].ordinal == 4
    sql, params = pool.conn.calls[0]
    assert "c.ordinal = ANY(%s::integer[])" in sql
    assert params[-1] == [4, 5]


@pytest.mark.parametrize("values", [("",), ("chunk", "chunk")])
def test_bad_or_duplicate_chunk_ids_fail_before_database(values):
    pool = Pool()

    with pytest.raises(RemoteRetrievalContractError, match="REMOTE_SOURCE_INPUT_INVALID"):
        PostgresRemoteSourceReader(pool).read_chunks(BINDING, values)

    assert pool.conn.calls == []


@pytest.mark.parametrize("ordinals", [(-1,), (1, 1), (True,)])
def test_bad_ordinals_fail_before_database(ordinals):
    pool = Pool()

    with pytest.raises(RemoteRetrievalContractError, match="REMOTE_SOURCE_INPUT_INVALID"):
        PostgresRemoteSourceReader(pool).following_chunks(BINDING, ordinals)

    assert pool.conn.calls == []


def test_database_error_is_redacted_and_never_falls_back_to_latest():
    pool = Pool(error=RuntimeError("private owner source and SQL details"))

    with pytest.raises(RemoteRetrievalContractError, match="^REMOTE_SOURCE_UNAVAILABLE$"):
        PostgresRemoteSourceReader(pool).read_chunks(BINDING, ("chunk",))


def test_fragment_lookup_uses_parent_membership_without_duplicating_rows():
    binding = replace(BINDING, model_fingerprint=json.dumps({
        "model": "synthetic",
        "split_policy": {"version": "token-window-v1", "payload_tokens": 112, "overlap_tokens": 24},
    }))
    pool = Pool((ROW,))
    rows = PostgresRemoteSourceReader(pool).read_chunks(binding, ("chunk",))
    assert len(rows) == 1 and rows[0].binding == binding
    sql, _ = pool.conn.calls[0]
    assert "EXISTS (SELECT 1 FROM server_embedding_fragments" in sql
    assert "ev.embedding_set_id=pub.embedding_set_id" in sql
    assert "ev.chunk_id=c.chunk_id" in sql


def test_malformed_fingerprint_fails_before_database():
    pool = Pool((ROW,))
    with pytest.raises(RemoteRetrievalContractError, match="^REMOTE_SOURCE_UNAVAILABLE$"):
        PostgresRemoteSourceReader(pool).read_chunks(
            replace(BINDING, model_fingerprint="invalid"), ("chunk",)
        )
    assert pool.conn.calls == []
