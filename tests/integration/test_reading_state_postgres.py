"""실제 PostgreSQL에서 읽기 상태의 CAS·재생·완료 보존 계약을 검증한다."""
# ruff: noqa: F811

from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4

import pytest

from solo_leveling.interfaces.http.papers import PaperNotFound, PostgresPaperStore
from solo_leveling.interfaces.http.reading_states import ReadingStateConflict
from tests.integration.test_remote_processing_postgres import db, make_paper, pool  # noqa: F401


pytestmark = pytest.mark.skipif(
    not os.environ.get("SOLO_TEST_POSTGRES_DSN"),
    reason="실제 PostgreSQL 테스트 DSN 필요",
)


def request(
    source_instance_id,
    *,
    operation_id=None,
    status="completed",
    action="complete",
    expected_revision=0,
    source_revision=1,
    basis_version_id="v1",
):
    return {
        "source_instance_id": source_instance_id,
        "operation_id": operation_id or str(uuid4()),
        "status": status,
        "action": action,
        "expected_revision": expected_revision,
        "source_revision": source_revision,
        "basis_version_id": basis_version_id,
    }


def test_concurrent_cas_replay_late_delivery_and_explicit_reopen(pool, db):
    owner = "reading-state-owner"
    paper_id = make_paper(db, owner)
    source_id = str(
        db(
            "SELECT source_instance_id FROM server_papers WHERE paper_id=%s",
            (paper_id,),
        )[0]["source_instance_id"]
    )
    store = PostgresPaperStore(pool)
    assert store.get_reading_state(owner, paper_id, source_id).revision == 0

    competing = [request(source_id), request(source_id)]

    def write(item):
        try:
            return store.put_reading_state(owner, paper_id, item), None
        except ReadingStateConflict as exc:
            return None, exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(write, competing))

    successes = [(item, result) for item, (result, error) in zip(competing, outcomes) if error is None]
    assert len(successes) == 1
    winner_request, winner = successes[0]
    assert winner.status == "completed"
    assert winner.revision == 1
    assert winner.source_revision == 1

    replay = store.put_reading_state(owner, paper_id, winner_request)
    assert replay == winner
    assert store.get_reading_state(owner, paper_id, source_id).revision == 1

    with pytest.raises(ReadingStateConflict):
        store.put_reading_state(
            owner,
            paper_id,
            winner_request | {"status": "reading", "action": "start"},
        )

    late_start = request(
        source_id,
        status="reading",
        action="start",
        expected_revision=1,
        source_revision=2,
    )
    with pytest.raises(ReadingStateConflict):
        store.put_reading_state(owner, paper_id, late_start)
    preserved = store.get_reading_state(owner, paper_id, source_id)
    assert preserved.status == "completed"
    assert preserved.revision == 1

    reopen = request(
        source_id,
        status="reading",
        action="reopen",
        expected_revision=1,
        source_revision=2,
    )
    reopened = store.put_reading_state(owner, paper_id, reopen)
    assert reopened.status == "reading"
    assert reopened.revision == 2
    assert reopened.basis_version_id == "v1"
    assert store.put_reading_state(owner, paper_id, reopen) == reopened

    completed_again = store.put_reading_state(
        owner,
        paper_id,
        request(
            source_id,
            expected_revision=2,
            source_revision=3,
        ),
    )
    assert completed_again.status == "completed"
    assert completed_again.revision == 3

    with pytest.raises(ReadingStateConflict):
        store.put_reading_state(
            owner,
            paper_id,
            request(
                source_id,
                status="reading",
                action="reopen",
                expected_revision=3,
                source_revision=2,
            ),
        )
    assert store.get_reading_state(owner, paper_id, source_id) == completed_again


def test_owner_and_source_boundaries_hide_existing_reading_state(pool, db):
    owner = "reading-state-boundary-owner"
    paper_id = make_paper(db, owner)
    source_id = str(
        db(
            "SELECT source_instance_id FROM server_papers WHERE paper_id=%s",
            (paper_id,),
        )[0]["source_instance_id"]
    )
    store = PostgresPaperStore(pool)
    saved = store.put_reading_state(owner, paper_id, request(source_id))
    assert saved.revision == 1

    for attempted_owner, attempted_source in (
        ("different-owner", source_id),
        (owner, str(uuid4())),
    ):
        with pytest.raises(PaperNotFound):
            store.get_reading_state(attempted_owner, paper_id, attempted_source)
        with pytest.raises(PaperNotFound):
            store.put_reading_state(
                attempted_owner,
                paper_id,
                request(attempted_source),
            )

    assert store.get_reading_state(owner, paper_id, source_id) == saved
