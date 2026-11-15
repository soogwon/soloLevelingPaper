"""실제 고정 출처 adapter의 원문·번역·접근 범위를 PostgreSQL에서 검증한다."""
# ruff: noqa: F811

from dataclasses import replace
import os

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_retrieval_postgres import seed
from solo_leveling.interfaces.http.remote_sources import PostgresRemoteSourceReader


pytestmark = pytest.mark.skipif(not os.environ.get("SOLO_TEST_POSTGRES_DSN"),
                                reason="실제 PostgreSQL+pgvector 테스트 DSN 필요")


def test_reader_keeps_pinned_original_translation_and_ordinal_after_republication(db, stores, pool):
    paper, revision, binding = seed(db, stores, pool, "alice", texts=("Old original.", "이전 번역."))
    _, new_revision, _ = seed(db, stores, pool, "alice", paper=paper, texts=("New original.", "새 번역."))
    reader = PostgresRemoteSourceReader(pool)
    rows = reader.read_chunks(binding, (revision[3][0], new_revision[3][0]))
    assert len(rows) == 1
    assert rows[0].binding == binding and rows[0].chunk_id == revision[3][0]
    assert rows[0].original_text == "Old original." and rows[0].translation_text == "이전 번역."
    following = reader.following_chunks(binding, (1,))
    assert len(following) == 1 and following[0].chunk_id == revision[3][1]


def test_reader_rejects_foreign_context_and_tombstone_even_with_old_binding(db, stores, pool):
    paper, revision, binding = seed(db, stores, pool, "alice")
    reader = PostgresRemoteSourceReader(pool)
    assert reader.read_chunks(replace(binding, owner_user_id="bob"), (revision[3][0],)) == ()
    db("UPDATE server_papers SET deleted_at=CURRENT_TIMESTAMP WHERE paper_id=%s", (paper,))
    assert reader.read_chunks(binding, (revision[3][0],)) == ()
    assert reader.following_chunks(binding, (1,)) == ()
