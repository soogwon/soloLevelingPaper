"""실제 PostgreSQL에서 읽기 상태의 CAS·재생·완료 보존 계약을 검증한다."""
# ruff: noqa: F811

from concurrent.futures import ThreadPoolExecutor
import os
from types import SimpleNamespace
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.auth import AccessTokenRejected, AuthenticatedPrincipal
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

    barrier = Barrier(2, timeout=15)

    def write(item):
        # 두 호출이 모두 준비된 후 실행해 우연한 순차 실행을 피한다.
        barrier.wait()
        try:
            return store.put_reading_state(owner, paper_id, item), None
        except ReadingStateConflict as exc:
            return None, exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(write, competing))

    successes = [(item, result) for item, (result, error) in zip(competing, outcomes) if error is None]
    assert len(successes) == 1
    assert sum(isinstance(error, ReadingStateConflict) for _, error in outcomes) == 1
    winner_request, winner = successes[0]
    assert winner.status == "completed"
    assert winner.revision == 1
    assert winner.source_revision == 1

    # 같은 operation의 동시 재생도 결과와 revision을 증가시키지 않는다.
    with ThreadPoolExecutor(max_workers=2) as executor:
        replayed = list(executor.map(write, [winner_request, winner_request]))
    assert all(error is None and result == winner for result, error in replayed)
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
    with pytest.raises(ReadingStateConflict):
        store.put_reading_state(owner, paper_id, reopen | {"basis_version_id": "v2"})
    assert store.get_reading_state(owner, paper_id, source_id) == preserved
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


def test_authenticated_http_boundaries_and_conflict_preserve_state(pool, db):
    owner = "reading-state-api-owner"
    paper_id = make_paper(db, owner)
    source_id = str(
        db(
            "SELECT source_instance_id FROM server_papers WHERE paper_id=%s",
            (paper_id,),
        )[0]["source_instance_id"]
    )
    store = PostgresPaperStore(pool)

    class TokenVerifier:
        def verify(self, token):
            principals = {
                "owner-device": AuthenticatedPrincipal(owner, "device", "owner-device"),
                "foreign-device": AuthenticatedPrincipal(
                    "reading-state-api-foreign",
                    "device",
                    "foreign-device",
                ),
                "owner-web": AuthenticatedPrincipal(owner, "web_session"),
            }
            try:
                return principals[token]
            except KeyError:
                raise AccessTokenRejected from None

    app = create_app(
        token_verifier=TokenVerifier(),
        device_registry=SimpleNamespace(is_active=lambda *_: True),
        paper_store=store,
    )
    path = f"/v1/mcp/papers/{paper_id}/reading-state"
    owner_headers = {"Authorization": "Bearer owner-device"}
    foreign_headers = {"Authorization": "Bearer foreign-device"}
    web_headers = {"Authorization": "Bearer owner-web"}
    complete = request(source_id)

    with TestClient(app) as client:
        # 보호할 완료 상태를 먼저 만들고 모든 거부 뒤 전체 응답이 같은지 확인한다.
        response = client.put(path, json=complete, headers=owner_headers)
        assert response.status_code == 200
        saved = response.json()
        assert saved["status"] == "completed"
        assert saved["revision"] == 1

        def assert_preserved():
            current = client.get(
                path, params={"source_instance_id": source_id}, headers=owner_headers,
            )
            assert current.status_code == 200
            assert current.json() == saved

        for headers, expected_status, code in (
            ({}, 401, "authentication_required"),
            ({"Authorization": "Bearer invalid"}, 401, "authentication_required"),
            (web_headers, 403, "device_credential_required"),
        ):
            for method in ("get", "put"):
                response = (
                    client.get(path, params={"source_instance_id": source_id}, headers=headers)
                    if method == "get"
                    else client.put(path, json=complete, headers=headers)
                )
                assert response.status_code == expected_status
                assert response.json() == {"detail": {"code": code}}
                assert_preserved()

        # 타인·다른 source와 미존재 대상이 같은 공개 오류를 반환한다.
        missing_path = f"/v1/mcp/papers/{uuid4()}/reading-state"
        for attempted_path, headers, attempted_source in (
            (path, foreign_headers, source_id),
            (path, owner_headers, str(uuid4())),
            (missing_path, owner_headers, source_id),
        ):
            for method in ("get", "put"):
                response = (
                    client.get(attempted_path, params={"source_instance_id": attempted_source}, headers=headers)
                    if method == "get"
                    else client.put(attempted_path, json=request(attempted_source), headers=headers)
                )
                assert response.status_code == 404
                assert response.json() == {"detail": {"code": "paper_not_found"}}
                assert_preserved()

        response = client.put(
            path,
            json=request(
                source_id,
                status="reading",
                action="start",
                expected_revision=1,
                source_revision=2,
            ),
            headers=owner_headers,
        )
        assert response.status_code == 409
        assert response.json() == {"detail": {"code": "reading_state_conflict"}}
        assert_preserved()

        # 인증 HTTP 경계에서도 같은 operation의 재생은 변경을 만들지 않는다.
        response = client.put(path, json=complete, headers=owner_headers)
        assert response.status_code == 200
        assert response.json() == saved
        assert_preserved()
