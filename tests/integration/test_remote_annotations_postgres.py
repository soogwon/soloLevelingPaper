# ruff: noqa: F811
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from solo_leveling.domain.annotation import SegmentInput
from solo_leveling.interfaces.http.remote_annotations import (
    PostgresRemoteAnnotationStore,
    RemoteAnnotationConflict,
    RemoteAnnotationDeleted,
    RemoteAnnotationInput,
    RemoteAnnotationReferenceError,
)
from solo_leveling.interfaces.http.papers import PostgresPaperStore
from tests.integration.test_remote_processing_postgres import (  # noqa: F401
    claimed_job,
    db,
    make_paper,
    make_revision_set,
    new_id,
    pool,
    publish,
    stores,
)


pytestmark = pytest.mark.skipif(
    not os.environ.get("SOLO_TEST_POSTGRES_DSN"), reason="실제 PostgreSQL DSN 필요"
)


@pytest.fixture
def context(db, stores):
    owner = new_id()
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,'합성 사용자')", (owner,))
    jobs, publications = stores
    paper_id = make_paper(db, owner)
    job = claimed_job(jobs, owner, paper_id)
    revisions = make_revision_set(db, owner, paper_id, job, chunks=1)
    publication = publish(publications, owner, paper_id, revisions, job)
    context_id = new_id()
    db(
        """INSERT INTO server_execution_contexts
               (context_id,owner_user_id,paper_id,server_version_id,publication_id,parse_revision_id)
           VALUES (%s,%s,%s,'v1',%s,%s)""",
        (context_id, owner, paper_id, publication.publication_id, revisions[0]),
    )
    return owner, context_id, revisions


def annotation(context, *, body="합성 메모", quote="synthetic", target="original"):
    translation_id = context[2][1] if target == "translation" else None
    return RemoteAnnotationInput(
        "note", body,
        (SegmentInput(context[2][3][0], 0, len(quote), quote, target, translation_id),),
        creation_origin="ai",
    )


def test_remote_annotation_is_visible_in_owner_global_web_library(pool, context):
    owner, context_id, _ = context
    remote = PostgresRemoteAnnotationStore(pool).create(
        owner, "paper-detail", context_id, "global-library", annotation(context),
    ).annotation

    page = PostgresPaperStore(pool).list_annotations(
        owner, kind=None, start_date=None, end_date=None, query=None,
        sort="newest", paper_id=None, version_id=None, page=1, limit=20,
    )

    found = next(item for item in page.items if item.annotation_id == remote.annotation_id)
    assert found.context_id == context_id
    assert found.storage_origin == "remote"
    assert found.paper_id
    assert not PostgresPaperStore(pool).list_annotations(
        "other-owner", kind=None, start_date=None, end_date=None, query=None,
        sort="newest", paper_id=None, version_id=None, page=1, limit=20,
    ).items


def test_concurrent_replay_is_one_annotation_and_returns_current_revision(pool, db, context):
    owner, context_id, _ = context
    store = PostgresRemoteAnnotationStore(pool)
    value = annotation(context)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(
            lambda _: store.create(owner, "paper-detail", context_id, "same-key", value), range(4)
        ))

    assert len({item.annotation.annotation_id for item in results}) == 1
    assert sum(not item.reused for item in results) == 1
    annotation_id = results[0].annotation.annotation_id
    db(
        """UPDATE server_context_annotations SET body='수정된 현재 본문',revision=2,
                  updated_at=CURRENT_TIMESTAMP WHERE owner_user_id=%s AND annotation_id=%s""",
        (owner, annotation_id),
    )
    replay = store.create(owner, "paper-detail", context_id, "same-key", value)
    assert replay.reused and replay.annotation.revision == 2
    assert replay.annotation.body == "수정된 현재 본문"
    assert db("SELECT count(*) AS n FROM server_context_annotations WHERE owner_user_id=%s", (owner,))[0]["n"] == 1


def test_changed_input_deleted_replay_and_scope_isolation(pool, db, context):
    owner, context_id, _ = context
    store = PostgresRemoteAnnotationStore(pool)
    saved = store.create(owner, "paper-detail", context_id, "fixed-key", annotation(context))
    with pytest.raises(RemoteAnnotationConflict, match="annotation_request_conflict"):
        store.create(owner, "paper-detail", context_id, "fixed-key", annotation(context, body="다름"))
    assert store.mark_deleted(owner, saved.annotation.annotation_id)
    with pytest.raises(RemoteAnnotationDeleted, match="annotation_deleted"):
        store.create(owner, "paper-detail", context_id, "fixed-key", annotation(context))
    with pytest.raises(RemoteAnnotationReferenceError, match="annotation_create_not_found"):
        store.create("other-owner", "paper-detail", context_id, "other-key", annotation(context))


@pytest.mark.parametrize("target,quote", [("original", "wrong text"), ("translation", "wrong text")])
def test_invalid_reference_does_not_consume_request_key(pool, db, context, target, quote):
    owner, context_id, _ = context
    store = PostgresRemoteAnnotationStore(pool)
    with pytest.raises(RemoteAnnotationReferenceError):
        store.create(owner, "paper-detail", context_id, "retry-key", annotation(context, target=target, quote=quote))
    assert db(
        """SELECT count(*) AS n FROM server_annotation_create_requests
           WHERE owner_user_id=%s AND context_id=%s AND request_key='retry-key'""",
        (owner, context_id),
    )[0]["n"] == 0
    assert store.create(owner, "paper-detail", context_id, "retry-key", annotation(context)).annotation


def test_ledger_insert_failure_rolls_back_annotation_and_key_can_be_retried(pool, db, context):
    owner, context_id, _ = context
    store = PostgresRemoteAnnotationStore(pool)
    db(
        """CREATE FUNCTION fail_remote_annotation_ledger() RETURNS trigger LANGUAGE plpgsql AS $$
           BEGIN
             IF NEW.request_key='rollback-key' THEN RAISE EXCEPTION 'synthetic ledger failure'; END IF;
             RETURN NEW;
           END $$"""
    )
    db(
        """CREATE TRIGGER fail_remote_annotation_ledger BEFORE INSERT
           ON server_annotation_create_requests FOR EACH ROW
           EXECUTE FUNCTION fail_remote_annotation_ledger()"""
    )
    try:
        with pytest.raises(Exception, match="synthetic ledger failure"):
            store.create(owner, "paper-detail", context_id, "rollback-key", annotation(context))
        assert db(
            "SELECT count(*) AS n FROM server_context_annotations WHERE owner_user_id=%s",
            (owner,),
        )[0]["n"] == 0
        assert db(
            "SELECT count(*) AS n FROM server_annotation_create_requests WHERE owner_user_id=%s",
            (owner,),
        )[0]["n"] == 0
    finally:
        db("DROP TRIGGER fail_remote_annotation_ledger ON server_annotation_create_requests")
        db("DROP FUNCTION fail_remote_annotation_ledger()")
    assert store.create(owner, "paper-detail", context_id, "rollback-key", annotation(context)).annotation


@pytest.mark.parametrize("operation", ["replay", "delete"])
def test_annotation_writes_wait_for_parent_before_locking_children(pool, db, context, operation):
    """부모를 기다리는 동안 자식 잠금을 잡아 물리 삭제와 교착하지 않는다."""
    import time
    from threading import Event

    owner, context_id, _ = context
    store = PostgresRemoteAnnotationStore(pool)
    value = annotation(context)
    saved = store.create(owner, "paper-detail", context_id, "parent-key", value)
    annotation_id = saved.annotation.annotation_id
    started = Event()

    def write():
        started.set()
        if operation == "delete":
            return store.mark_deleted(owner, annotation_id)
        return store.create(owner, "paper-detail", context_id, "parent-key", value)

    with ThreadPoolExecutor(max_workers=1) as executor:
        with pool.connection() as parent:
            parent.execute(
                """SELECT p.paper_id FROM server_papers p JOIN server_execution_contexts c
                   ON c.owner_user_id=p.owner_user_id AND c.paper_id=p.paper_id
                   WHERE c.owner_user_id=%s AND c.context_id=%s FOR UPDATE OF p""",
                (owner, context_id),
            )
            future = executor.submit(write)
            assert started.wait(5)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if future.done():
                    pytest.fail("부모 잠금 해제 전에 쓰기가 완료됨")
                parent.execute("SELECT pg_stat_clear_snapshot()")
                waiting = parent.execute(
                    "SELECT count(*) AS n FROM pg_stat_activity WHERE pid <> pg_backend_pid() "
                    "AND wait_event_type='Lock' AND %s=ANY(pg_blocking_pids(pid))",
                    (parent.info.backend_pid,),
                ).fetchone()["n"]
                if waiting:
                    break
                time.sleep(0.01)
            else:
                pytest.fail("부모 잠금 대기 미관측")
            parent.execute("SET LOCAL lock_timeout='1s'")
            parent.execute(
                "SELECT annotation_id FROM server_context_annotations "
                "WHERE owner_user_id=%s AND annotation_id=%s FOR UPDATE NOWAIT",
                (owner, annotation_id),
            )
            parent.execute(
                "SELECT annotation_id FROM server_annotation_create_requests "
                "WHERE owner_user_id=%s AND annotation_id=%s FOR UPDATE NOWAIT",
                (owner, annotation_id),
            )
        result = future.result(timeout=5)
    assert result is True if operation == "delete" else result.reused


def test_replay_and_delete_converge_without_deadlock(pool, context):
    from threading import Barrier

    owner, context_id, _ = context
    store = PostgresRemoteAnnotationStore(pool)
    value = annotation(context)
    saved = store.create(owner, "paper-detail", context_id, "race-key", value)
    barrier = Barrier(2)

    def replay():
        barrier.wait(timeout=5)
        try:
            return store.create(owner, "paper-detail", context_id, "race-key", value).reused
        except RemoteAnnotationDeleted:
            return True

    def delete():
        barrier.wait(timeout=5)
        return store.mark_deleted(owner, saved.annotation.annotation_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(replay), executor.submit(delete)
        assert first.result(timeout=10) and second.result(timeout=10)
    with pytest.raises(RemoteAnnotationDeleted):
        store.create(owner, "paper-detail", context_id, "race-key", value)


def test_find_original_without_saved_evidence_and_store_highlight_note_together(pool, context):
    from solo_leveling.interfaces.http.remote_highlight_lookup import find_highlight_candidates

    owner, context_id, _ = context
    results = find_highlight_candidates(pool, owner, context_id, "synthetic")
    assert results
    selected = results[0]
    assert selected["segment"]["quote_snapshot"] == "synthetic"
    assert find_highlight_candidates(pool, owner, context_id, "synthetic", selected["pdf_page"]) == results
    assert find_highlight_candidates(pool, owner, context_id, "missing quote") == []
    assert find_highlight_candidates(pool, new_id(), context_id, "synthetic") == []
    value = RemoteAnnotationInput("highlight", "12개의 신호라는 것은 매우 중요하다. 더 확인할 필요 있음",
                                  (SegmentInput(**selected["segment"]),), creation_origin="user")
    store = PostgresRemoteAnnotationStore(pool)
    assert store.list_annotations(owner, context_id)["items"] == []
    first = store.create(owner, "paper-detail", context_id, "quote-note", value)
    second = store.create(owner, "paper-detail", context_id, "quote-note", value)
    assert second.reused and second.annotation.annotation_id == first.annotation.annotation_id
    saved, = store.list_annotations(owner, context_id)["items"]
    assert saved.kind == "highlight" and saved.body == value.body
    assert saved.segments[0]["quote_snapshot"] == "synthetic"
    updated = store.update_annotation(owner, context_id, saved.annotation_id, body="", step=None,
                                      expected_revision=saved.revision)
    assert updated.body == "" and updated.segments == saved.segments
    paired = store.update_annotation(owner, context_id, updated.annotation_id, body=value.body, step=None,
                                     expected_revision=updated.revision)
    store.delete_annotation(owner, context_id, paired.annotation_id, expected_revision=paired.revision)
    assert store.list_annotations(owner, context_id)["items"] == []
