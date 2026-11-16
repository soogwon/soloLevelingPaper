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
