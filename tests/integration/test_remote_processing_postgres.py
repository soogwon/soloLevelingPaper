"""원격 처리 스키마와 저장소를 실제 PostgreSQL에서 확인한다.

단위 테스트는 가짜 연결을 쓰므로 FK, 트리거, SQL 문법 오류를 잡지 못한다.
SOLO_TEST_POSTGRES_DSN이 있을 때만 실행하며, 임시 스키마에 서버 마이그레이션 전체를 적용한 뒤 지운다.
합성 데이터만 사용한다.
"""

import json
import os
from pathlib import Path
import time
from uuid import uuid4

import pytest


DSN = os.environ.get("SOLO_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="SOLO_TEST_POSTGRES_DSN이 필요한 실제 PostgreSQL 검증")

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations" / "server"
HASH = "a" * 64


def new_id():
    return str(uuid4())


@pytest.fixture(scope="module")
def pool():
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    schema = f"remote_processing_test_{uuid4().hex[:12]}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f'SET search_path TO "{schema}"')
        for path in sorted(MIGRATIONS.glob("0*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
    value = ConnectionPool(
        DSN,
        kwargs={"row_factory": dict_row, "options": f"-c search_path={schema}"},
        min_size=1,
        max_size=4,
        open=True,
    )
    try:
        yield value
    finally:
        value.close()
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture
def db(pool):
    def run(sql, params=()):
        with pool.connection() as conn:
            cursor = conn.execute(sql, params)
            return cursor.fetchall() if cursor.description else None

    return run


@pytest.fixture
def stores(pool):
    from solo_leveling.interfaces.http.processing_jobs import PostgresProcessingJobStore
    from solo_leveling.interfaces.http.processing_publications import (
        PostgresProcessingPublicationStore,
    )

    return PostgresProcessingJobStore(pool), PostgresProcessingPublicationStore(pool)


def make_paper(db, owner):
    paper_id = new_id()
    db(
        """INSERT INTO server_papers
               (paper_id, owner_user_id, source_instance_id, local_paper_id, original_created_at)
           VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)""",
        (paper_id, owner, new_id(), new_id()),
    )
    db(
        """INSERT INTO server_processing_versions (owner_user_id, paper_id, server_version_id)
           VALUES (%s, %s, 'v1')""",
        (owner, paper_id),
    )
    return paper_id


def insert_chunk(db, owner, paper_id, parse_id, ordinal, chunk_id=None):
    chunk_id = chunk_id or new_id()
    db(
        """INSERT INTO server_processing_chunks
               (chunk_id, owner_user_id, paper_id, server_version_id, parse_revision_id,
                ordinal, pdf_page, original_text)
           VALUES (%s, %s, %s, 'v1', %s, %s, 1, 'synthetic')""",
        (chunk_id, owner, paper_id, parse_id, ordinal),
    )
    return chunk_id


def make_revision_set(db, owner, paper_id, *, chunks=3, translated=None, omissions=None, ready=True):
    """building 상태로 적재한 뒤 ready로 확정한다. 실제 worker의 순서와 같다."""
    translated = chunks if translated is None else translated
    parse_id, translation_id, embedding_id = new_id(), new_id(), new_id()
    db(
        """INSERT INTO server_parse_revisions
               (parse_revision_id, owner_user_id, paper_id, server_version_id, status, chunk_count)
           VALUES (%s, %s, %s, 'v1', 'building', %s)""",
        (parse_id, owner, paper_id, chunks),
    )
    chunk_ids = [insert_chunk(db, owner, paper_id, parse_id, n) for n in range(chunks)]
    omitted = omissions(chunk_ids) if omissions else []
    db(
        """INSERT INTO server_translation_revisions
               (translation_revision_id, owner_user_id, paper_id, server_version_id,
                parse_revision_id, status, source_chunk_count, translated_chunk_count, omissions)
           VALUES (%s, %s, %s, 'v1', %s, 'building', %s, %s, %s::jsonb)""",
        (translation_id, owner, paper_id, parse_id, chunks, translated, json.dumps(omitted)),
    )
    for chunk_id in chunk_ids[:translated]:
        db(
            """INSERT INTO server_translation_chunks
                   (owner_user_id, paper_id, server_version_id, parse_revision_id,
                    translation_revision_id, chunk_id, translated_text)
               VALUES (%s, %s, 'v1', %s, %s, %s, 'synthetic')""",
            (owner, paper_id, parse_id, translation_id, chunk_id),
        )
    db(
        """INSERT INTO server_embedding_sets
               (embedding_set_id, owner_user_id, paper_id, server_version_id, parse_revision_id,
                translation_revision_id, status, indexed_chunk_count, model_fingerprint)
           VALUES (%s, %s, %s, 'v1', %s, %s, 'building', %s, '{}'::jsonb)""",
        (embedding_id, owner, paper_id, parse_id, translation_id, translated),
    )
    if ready:
        for table, key, value in (
            ("server_parse_revisions", "parse_revision_id", parse_id),
            ("server_translation_revisions", "translation_revision_id", translation_id),
            ("server_embedding_sets", "embedding_set_id", embedding_id),
        ):
            db(
                f"UPDATE {table} SET status='ready', completed_at=CURRENT_TIMESTAMP WHERE {key}=%s",
                (value,),
            )
    return parse_id, translation_id, embedding_id, chunk_ids


def claimed_job(jobs, owner, paper_id, **options):
    jobs.enqueue(owner, paper_id, "v1", "full_processing", new_id(), HASH, **options)
    return jobs.claim("worker")


def publish(publications, owner, paper_id, revision_set, job, policy="complete"):
    parse_id, translation_id, embedding_id, _ = revision_set
    return publications.publish(
        owner, paper_id, "v1", parse_id, translation_id, embedding_id,
        job.job_id, job.lease_token, policy=policy,
    )


def test_publishes_and_rejects_cross_owner_links(db, stores):
    import psycopg
    from solo_leveling.interfaces.http.processing_jobs import ProcessingJobNotFound
    from solo_leveling.interfaces.http.processing_publications import ProcessingPublicationNotFound

    jobs, publications = stores
    alice_paper, bob_paper = make_paper(db, "alice"), make_paper(db, "bob")
    revision_set = make_revision_set(db, "alice", alice_paper)
    job = claimed_job(jobs, "alice", alice_paper)

    published = publish(publications, "alice", alice_paper, revision_set, job)
    jobs.succeed("alice", job.job_id, job.lease_token)

    assert publications.get_active("alice", alice_paper, "v1").publication_id == published.publication_id
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        insert_chunk(db, "bob", bob_paper, revision_set[0], 99)
    with pytest.raises(ProcessingPublicationNotFound):
        publications.get_active("bob", alice_paper, "v1")
    with pytest.raises(ProcessingPublicationNotFound):
        publish(publications, "bob", alice_paper, revision_set, job)
    with pytest.raises(ProcessingJobNotFound):
        jobs.enqueue("bob", alice_paper, "v1", "parse", new_id(), HASH)


def test_expired_worker_cannot_publish_finish_or_heartbeat(db, stores):
    from solo_leveling.interfaces.http.processing_jobs import ProcessingJobLeaseLost
    from solo_leveling.interfaces.http.processing_publications import ProcessingPublicationNotFound

    jobs, publications = stores
    paper_id = make_paper(db, "alice")
    revision_set = make_revision_set(db, "alice", paper_id)
    jobs.enqueue("alice", paper_id, "v1", "full_processing", new_id(), HASH)
    old = jobs.claim("worker-1", lease_seconds=1)
    time.sleep(1.2)
    new = jobs.claim("worker-2")

    assert new.job_id == old.job_id and new.attempt == 2 and new.lease_token != old.lease_token
    with pytest.raises(ProcessingPublicationNotFound):
        publish(publications, "alice", paper_id, revision_set, old)
    with pytest.raises(ProcessingJobLeaseLost):
        jobs.succeed("alice", old.job_id, old.lease_token)
    with pytest.raises(ProcessingJobLeaseLost):
        jobs.heartbeat("alice", old.job_id, old.lease_token)

    jobs.cancel("alice", new.job_id)
    with pytest.raises(ProcessingPublicationNotFound):
        publish(publications, "alice", paper_id, revision_set, new)


@pytest.mark.parametrize(
    ("options", "error_code"),
    [
        ({"retryable": False, "max_attempts": 3}, "lease_expired_not_retryable"),
        ({"retryable": True, "max_attempts": 1}, "max_attempts_exhausted"),
    ],
)
def test_expired_job_that_cannot_be_reclaimed_becomes_failed(db, stores, options, error_code):
    jobs, _ = stores
    paper_id = make_paper(db, "alice")
    request_key = new_id()
    jobs.enqueue("alice", paper_id, "v1", "translate", request_key, HASH, **options)
    assert jobs.claim("worker-1", lease_seconds=1) is not None
    time.sleep(1.2)

    assert jobs.claim("worker-2") is None
    row = db(
        "SELECT status, error_code, lease_token FROM server_processing_jobs WHERE request_key=%s",
        (request_key,),
    )[0]
    assert (row["status"], row["error_code"], row["lease_token"]) == ("failed", error_code, None)


@pytest.mark.parametrize(
    ("translated", "omissions", "reason"),
    [
        (2, lambda ids: [{"chunk_id": new_id(), "reason": "x"}], "translation_omission_mismatch"),
        (2, lambda ids: [{"chunk_id": ids[0], "reason": "x"}], "translation_omission_mismatch"),
        (2, lambda ids: [{"chunk_id": "not-a-uuid", "reason": "x"}], "translation_omission_mismatch"),
        (2, lambda ids: ["not-an-object"], "translation_omission_invalid"),
        (0, lambda ids: [{"chunk_id": value, "reason": "x"} for value in ids], "has_no_translation"),
    ],
)
def test_partial_publication_rejects_omissions_that_do_not_match(db, stores, translated, omissions, reason):
    from solo_leveling.interfaces.http.processing_publications import ProcessingPublicationNotReady

    jobs, publications = stores
    paper_id = make_paper(db, "alice")
    revision_set = make_revision_set(db, "alice", paper_id, translated=translated, omissions=omissions)
    job = claimed_job(jobs, "alice", paper_id)

    with pytest.raises(ProcessingPublicationNotReady, match=reason):
        publish(publications, "alice", paper_id, revision_set, job, policy="partial")


def test_partial_publication_accepts_exact_untranslated_chunks(db, stores):
    jobs, publications = stores
    paper_id = make_paper(db, "alice")
    revision_set = make_revision_set(
        db, "alice", paper_id, translated=2,
        omissions=lambda ids: [{"chunk_id": ids[2], "reason": "provider_error"}],
    )
    job = claimed_job(jobs, "alice", paper_id)

    result = publish(publications, "alice", paper_id, revision_set, job, policy="partial")

    assert result.manifest["translated_chunk_count"] == 2
    assert [item["chunk_id"] for item in result.manifest["omissions"]] == [revision_set[3][2]]


def test_finalized_revisions_reject_late_writes_but_building_ones_accept(db, stores):
    import psycopg

    paper_id = make_paper(db, "alice")
    parse_id, translation_id, _, chunk_ids = make_revision_set(db, "alice", paper_id)
    building_parse_id, *_ = make_revision_set(db, "alice", paper_id, ready=False)

    insert_chunk(db, "alice", paper_id, building_parse_id, 50)
    late_writes = [
        lambda: insert_chunk(db, "alice", paper_id, parse_id, 50),
        lambda: db("UPDATE server_processing_chunks SET original_text='late' WHERE chunk_id=%s",
                   (chunk_ids[0],)),
        lambda: db("DELETE FROM server_processing_chunks WHERE chunk_id=%s", (chunk_ids[0],)),
        lambda: db("UPDATE server_translation_chunks SET translated_text='late' WHERE chunk_id=%s",
                   (chunk_ids[0],)),
        lambda: db("UPDATE server_parse_revisions SET status='building', completed_at=NULL "
                   "WHERE parse_revision_id=%s", (parse_id,)),
        lambda: db("UPDATE server_translation_revisions SET omissions='[]'::jsonb "
                   "WHERE translation_revision_id=%s", (translation_id,)),
    ]
    for write in late_writes:
        with pytest.raises(psycopg.errors.CheckViolation, match="processing_revision_finalized"):
            write()


def test_deleting_a_paper_removes_published_processing_data(db, stores):
    import psycopg

    jobs, publications = stores
    paper_id = make_paper(db, "alice")
    revision_set = make_revision_set(db, "alice", paper_id)
    job = claimed_job(jobs, "alice", paper_id)
    published = publish(publications, "alice", paper_id, revision_set, job)
    context_id = new_id()
    db(
        """INSERT INTO server_execution_contexts
               (context_id, owner_user_id, paper_id, server_version_id, publication_id, parse_revision_id)
           VALUES (%s, 'alice', %s, 'v1', %s, %s)""",
        (context_id, paper_id, published.publication_id, revision_set[0]),
    )
    db(
        """INSERT INTO server_processing_evidences
               (evidence_id, owner_user_id, paper_id, server_version_id, publication_id,
                parse_revision_id, context_id, chunk_id, quoted_text)
           VALUES (%s, 'alice', %s, 'v1', %s, %s, %s, %s, 'synthetic')""",
        (new_id(), paper_id, published.publication_id, revision_set[0], context_id, revision_set[3][0]),
    )

    # 참조 중인 revision만 따로 지우는 것은 계속 거부한다.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db("DELETE FROM server_parse_revisions WHERE parse_revision_id=%s", (revision_set[0],))

    db("DELETE FROM server_papers WHERE paper_id=%s", (paper_id,))

    for table in (
        "server_processing_versions", "server_parse_revisions", "server_processing_chunks",
        "server_translation_revisions", "server_translation_chunks", "server_embedding_sets",
        "server_processing_publications", "server_execution_contexts",
        "server_processing_evidences", "server_processing_jobs",
    ):
        assert db(f"SELECT count(*) AS n FROM {table} WHERE paper_id=%s", (paper_id,))[0]["n"] == 0, table
