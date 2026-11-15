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


def claimed_job(jobs, owner, paper_id, *, worker="worker", lease_seconds=60, **options):
    jobs.enqueue(owner, paper_id, "v1", "full_processing", new_id(), HASH, **options)
    return jobs.claim(worker, lease_seconds=lease_seconds)


def insert_revisions(db, owner, paper_id, job, *, chunks=3, translated=None):
    """실행 세대(job, lease token)에 묶인 building revision 세 개를 만든다."""
    translated = chunks if translated is None else translated
    parse_id, translation_id, embedding_id = new_id(), new_id(), new_id()
    generation = (job.job_id, job.lease_token)
    db(
        """INSERT INTO server_parse_revisions
               (parse_revision_id, owner_user_id, paper_id, server_version_id, status, chunk_count,
                source_job_id, source_lease_token)
           VALUES (%s, %s, %s, 'v1', 'building', %s, %s, %s)""",
        (parse_id, owner, paper_id, chunks, *generation),
    )
    db(
        """INSERT INTO server_translation_revisions
               (translation_revision_id, owner_user_id, paper_id, server_version_id,
                parse_revision_id, status, source_chunk_count, translated_chunk_count,
                source_job_id, source_lease_token)
           VALUES (%s, %s, %s, 'v1', %s, 'building', %s, %s, %s, %s)""",
        (translation_id, owner, paper_id, parse_id, chunks, translated, *generation),
    )
    db(
        """INSERT INTO server_embedding_sets
               (embedding_set_id, owner_user_id, paper_id, server_version_id, parse_revision_id,
                translation_revision_id, status, indexed_chunk_count, model_fingerprint,
                source_job_id, source_lease_token)
           VALUES (%s, %s, %s, 'v1', %s, %s, 'building', %s, '{}'::jsonb, %s, %s)""",
        (embedding_id, owner, paper_id, parse_id, translation_id, translated, *generation),
    )
    return parse_id, translation_id, embedding_id


def insert_translation(db, owner, paper_id, parse_id, translation_id, chunk_id):
    db(
        """INSERT INTO server_translation_chunks
               (owner_user_id, paper_id, server_version_id, parse_revision_id,
                translation_revision_id, chunk_id, translated_text)
           VALUES (%s, %s, 'v1', %s, %s, %s, 'synthetic')""",
        (owner, paper_id, parse_id, translation_id, chunk_id),
    )


def mark_ready(db, parse_id, translation_id, embedding_id):
    for table, key, value in (
        ("server_parse_revisions", "parse_revision_id", parse_id),
        ("server_translation_revisions", "translation_revision_id", translation_id),
        ("server_embedding_sets", "embedding_set_id", embedding_id),
    ):
        db(
            f"UPDATE {table} SET status='ready', completed_at=CURRENT_TIMESTAMP WHERE {key}=%s",
            (value,),
        )


def make_revision_set(db, owner, paper_id, job, *, chunks=3, translated=None, omissions=None,
                      ready=True):
    """building 상태로 적재한 뒤 ready로 확정한다. 실제 worker의 순서와 같다."""
    translated = chunks if translated is None else translated
    parse_id, translation_id, embedding_id = insert_revisions(
        db, owner, paper_id, job, chunks=chunks, translated=translated
    )
    chunk_ids = [insert_chunk(db, owner, paper_id, parse_id, n) for n in range(chunks)]
    if omissions:
        db(
            "UPDATE server_translation_revisions SET omissions=%s::jsonb "
            "WHERE translation_revision_id=%s",
            (json.dumps(omissions(chunk_ids)), translation_id),
        )
    for chunk_id in chunk_ids[:translated]:
        insert_translation(db, owner, paper_id, parse_id, translation_id, chunk_id)
    if ready:
        mark_ready(db, parse_id, translation_id, embedding_id)
    return parse_id, translation_id, embedding_id, chunk_ids


def publish(publications, owner, paper_id, revision_set, job, policy="complete"):
    parse_id, translation_id, embedding_id, _ = revision_set
    return publications.publish(
        owner, paper_id, "v1", parse_id, translation_id, embedding_id,
        job.job_id, job.lease_token, policy=policy,
    )


def count_publications(db, job):
    return db(
        "SELECT count(*) AS n FROM server_processing_publications WHERE source_job_id=%s",
        (job.job_id,),
    )[0]["n"]


def job_status(db, job):
    return db("SELECT status FROM server_processing_jobs WHERE job_id=%s", (job.job_id,))[0]["status"]


def test_publishes_and_rejects_cross_owner_links(db, stores):
    import psycopg
    from solo_leveling.interfaces.http.processing_jobs import ProcessingJobNotFound
    from solo_leveling.interfaces.http.processing_publications import ProcessingPublicationNotFound

    jobs, publications = stores
    alice_paper, bob_paper = make_paper(db, "alice"), make_paper(db, "bob")
    job = claimed_job(jobs, "alice", alice_paper)
    bob_job = claimed_job(jobs, "bob", bob_paper)
    revision_set = make_revision_set(db, "alice", alice_paper, job)

    published = publish(publications, "alice", alice_paper, revision_set, job)

    assert publications.get_active("alice", alice_paper, "v1").publication_id == published.publication_id
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        insert_chunk(db, "bob", bob_paper, revision_set[0], 99)
    # 다른 사용자의 job을 실행 세대로 내세워 revision을 만들 수도 없다.
    with pytest.raises(psycopg.errors.CheckViolation, match="processing_lease_lost"):
        insert_revisions(db, "alice", alice_paper, bob_job)
    with pytest.raises(ProcessingPublicationNotFound):
        publications.get_active("bob", alice_paper, "v1")
    with pytest.raises(ProcessingPublicationNotFound):
        publish(publications, "bob", alice_paper, revision_set, job)
    with pytest.raises(ProcessingJobNotFound):
        jobs.enqueue("bob", alice_paper, "v1", "parse", new_id(), HASH)


def test_publication_completes_the_job_and_replays_for_the_same_generation(db, stores):
    """게시 직후 worker가 죽어도 작업이 다시 claim되지 않고, 같은 요청은 같은 게시를 돌려준다."""
    from solo_leveling.interfaces.http.processing_jobs import ProcessingJobLeaseLost
    from solo_leveling.interfaces.http.processing_publications import ProcessingPublicationNotFound

    jobs, publications = stores
    paper_id = make_paper(db, "alice")
    job = claimed_job(jobs, "alice", paper_id, lease_seconds=1)
    revision_set = make_revision_set(db, "alice", paper_id, job)

    published = publish(publications, "alice", paper_id, revision_set, job)
    time.sleep(1.2)  # 게시 직후 worker가 죽고 lease 시간이 지난 상황

    assert job_status(db, job) == "succeeded"
    assert jobs.claim("worker-2") is None
    assert publish(publications, "alice", paper_id, revision_set, job).publication_id == (
        published.publication_id
    )
    assert count_publications(db, job) == 1
    # 다른 실행 세대나 다른 정책으로는 재생되지 않는다.
    other = type(job)(**{**job.__dict__, "lease_token": new_id()})
    with pytest.raises(ProcessingPublicationNotFound):
        publish(publications, "alice", paper_id, revision_set, other)
    with pytest.raises(ProcessingPublicationNotFound):
        publish(publications, "alice", paper_id, revision_set, job, policy="partial")
    with pytest.raises(ProcessingJobLeaseLost):
        jobs.succeed("alice", job.job_id, job.lease_token)


def test_expired_worker_cannot_publish_finish_or_heartbeat(db, stores):
    from solo_leveling.interfaces.http.processing_jobs import ProcessingJobLeaseLost
    from solo_leveling.interfaces.http.processing_publications import ProcessingPublicationNotFound

    jobs, publications = stores
    paper_id = make_paper(db, "alice")
    old = claimed_job(jobs, "alice", paper_id, worker="worker-1", lease_seconds=1)
    revision_set = make_revision_set(db, "alice", paper_id, old)
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
    assert count_publications(db, old) == 0


def test_previous_generation_cannot_write_building_revisions_after_reclaim(db, stores):
    """lease가 끝나 회수된 뒤에는 이전 worker가 building revision에도 쓰지 못한다."""
    import psycopg

    jobs, _ = stores
    paper_id = make_paper(db, "alice")
    old = claimed_job(jobs, "alice", paper_id, worker="worker-1", lease_seconds=1)
    parse_id, translation_id, embedding_id = insert_revisions(db, "alice", paper_id, old)
    chunk_id = insert_chunk(db, "alice", paper_id, parse_id, 0)
    insert_translation(db, "alice", paper_id, parse_id, translation_id, chunk_id)
    time.sleep(1.2)
    new = jobs.claim("worker-2")
    assert new.lease_token != old.lease_token

    late_writes = [
        lambda: insert_chunk(db, "alice", paper_id, parse_id, 1),
        lambda: db("UPDATE server_processing_chunks SET original_text='late' WHERE chunk_id=%s",
                   (chunk_id,)),
        lambda: db("DELETE FROM server_processing_chunks WHERE chunk_id=%s", (chunk_id,)),
        lambda: db("UPDATE server_translation_chunks SET translated_text='late' WHERE chunk_id=%s",
                   (chunk_id,)),
        lambda: mark_ready(db, parse_id, translation_id, embedding_id),
        lambda: db("UPDATE server_embedding_sets SET indexed_chunk_count=1 WHERE embedding_set_id=%s",
                   (embedding_id,)),
        lambda: insert_revisions(db, "alice", paper_id, old),
    ]
    for write in late_writes:
        with pytest.raises(psycopg.errors.CheckViolation, match="processing_lease_lost"):
            write()

    # 새 실행 세대는 자기 revision을 새로 만들어 쓴다. 이전 세대의 것을 넘겨받지 않는다.
    with pytest.raises(psycopg.errors.CheckViolation, match="processing_revision_generation_fixed"):
        db(
            "UPDATE server_parse_revisions SET source_lease_token=%s WHERE parse_revision_id=%s",
            (new.lease_token, parse_id),
        )
    new_parse_id, *_ = insert_revisions(db, "alice", paper_id, new)
    insert_chunk(db, "alice", paper_id, new_parse_id, 0)
    # 버려진 building revision은 lease 없이도 failed로 닫을 수 있다.
    db("UPDATE server_parse_revisions SET status='failed' WHERE parse_revision_id=%s", (parse_id,))


def test_revisions_require_a_running_generation_and_start_building(db, stores):
    import psycopg

    jobs, _ = stores
    paper_id = make_paper(db, "alice")
    job = claimed_job(jobs, "alice", paper_id)

    with pytest.raises(psycopg.errors.CheckViolation, match="processing_revision_requires_job"):
        db(
            """INSERT INTO server_parse_revisions
                   (parse_revision_id, owner_user_id, paper_id, server_version_id, status, chunk_count)
               VALUES (%s, 'alice', %s, 'v1', 'building', 0)""",
            (new_id(), paper_id),
        )
    with pytest.raises(psycopg.errors.CheckViolation, match="processing_revision_must_start_building"):
        db(
            """INSERT INTO server_parse_revisions
                   (parse_revision_id, owner_user_id, paper_id, server_version_id, status, chunk_count,
                    completed_at, source_job_id, source_lease_token)
               VALUES (%s, 'alice', %s, 'v1', 'ready', 0, CURRENT_TIMESTAMP, %s, %s)""",
            (new_id(), paper_id, job.job_id, job.lease_token),
        )
    jobs.cancel("alice", job.job_id)
    with pytest.raises(psycopg.errors.CheckViolation, match="processing_lease_lost"):
        insert_revisions(db, "alice", paper_id, job)


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
    job = claimed_job(jobs, "alice", paper_id, worker="worker-1", lease_seconds=1, **options)
    assert job is not None
    time.sleep(1.2)

    assert jobs.claim("worker-2") is None
    row = db(
        "SELECT status, error_code, lease_token FROM server_processing_jobs WHERE job_id=%s",
        (job.job_id,),
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
    job = claimed_job(jobs, "alice", paper_id)
    revision_set = make_revision_set(
        db, "alice", paper_id, job, translated=translated, omissions=omissions
    )

    with pytest.raises(ProcessingPublicationNotReady, match=reason):
        publish(publications, "alice", paper_id, revision_set, job, policy="partial")
    # 거부된 게시는 작업을 완료 처리하지 않는다.
    assert job_status(db, job) == "running"
    jobs.cancel("alice", job.job_id)


def test_partial_publication_accepts_exact_untranslated_chunks(db, stores):
    jobs, publications = stores
    paper_id = make_paper(db, "alice")
    job = claimed_job(jobs, "alice", paper_id)
    revision_set = make_revision_set(
        db, "alice", paper_id, job, translated=2,
        omissions=lambda ids: [{"chunk_id": ids[2], "reason": "provider_error"}],
    )

    result = publish(publications, "alice", paper_id, revision_set, job, policy="partial")

    assert result.manifest["translated_chunk_count"] == 2
    assert [item["chunk_id"] for item in result.manifest["omissions"]] == [revision_set[3][2]]


def test_finalized_revisions_reject_late_writes_but_building_ones_accept(db, stores):
    import psycopg

    jobs, _ = stores
    paper_id = make_paper(db, "alice")
    job = claimed_job(jobs, "alice", paper_id)
    parse_id, translation_id, _, chunk_ids = make_revision_set(db, "alice", paper_id, job)
    building_parse_id, *_ = make_revision_set(db, "alice", paper_id, job, ready=False)

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
    job = claimed_job(jobs, "alice", paper_id)
    revision_set = make_revision_set(db, "alice", paper_id, job)
    published = publish(publications, "alice", paper_id, revision_set, job)
    # 삭제 시점에 실행 중이던 다른 작업과 그 building revision도 함께 지워져야 한다.
    running = claimed_job(jobs, "alice", paper_id)
    make_revision_set(db, "alice", paper_id, running, ready=False)
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
