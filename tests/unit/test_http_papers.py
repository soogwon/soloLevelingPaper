"""논문 메타데이터의 사용자 격리와 멱등 등록을 검증한다."""

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
from solo_leveling.interfaces.http.papers import (
    IdempotencyConflict,
    PaperMetadata,
    PaperRegistration,
    PostgresPaperStore,
    _payload_hash,
)


NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
SOURCE_ID = "00000000-0000-0000-0000-000000000101"
PAPER_ID = "00000000-0000-0000-0000-000000000201"


class Result:
    def __init__(self, one=None):
        self.one = one

    def fetchone(self):
        return self.one


class Connection:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((" ".join(sql.split()), params))
        return self.results.pop(0)


class Pool:
    def __init__(self, results):
        self.conn = Connection(results)

    def connection(self):
        return nullcontext(self.conn)


def metadata(title="Paper"):
    return PaperMetadata(SOURCE_ID, "local-paper-1", title, ("A", "B"), 2024, NOW)


def test_first_registration_uses_owner_scope_and_stores_idempotent_response():
    item = metadata()
    row = {"paper_id": PAPER_ID, "revision": 1, "updated_at": NOW}
    pool = Pool([
        Result(),
        Result({"payload_hash": _payload_hash(item), "response_status": None, "response_body": None}),
        Result(row),
        Result(),
    ])

    result = PostgresPaperStore(pool).register("user-1", "operation-key-0001", item)

    assert result == PaperRegistration(PAPER_ID, 1, "stored", NOW)
    assert pool.conn.calls[0][1][:3] == ("user-1", "register_paper", "operation-key-0001")
    assert pool.conn.calls[1][1] == ("user-1", "register_paper", "operation-key-0001")
    insert = pool.conn.calls[2]
    assert "ON CONFLICT (owner_user_id, source_instance_id, local_paper_id) DO NOTHING" in insert[0]
    assert insert[1][1:4] == ("user-1", SOURCE_ID, "local-paper-1")
    assert pool.conn.calls[3][1][1:] == ("user-1", "register_paper", "operation-key-0001")


def test_same_key_and_payload_replays_saved_response_without_touching_papers():
    item = metadata()
    body = {"paper_id": PAPER_ID, "revision": 1, "sync_status": "stored", "server_updated_at": NOW.isoformat()}
    pool = Pool([
        Result(),
        Result({"payload_hash": _payload_hash(item), "response_status": 201, "response_body": body}),
    ])

    result = PostgresPaperStore(pool).register("user-1", "operation-key-0001", item)

    assert result.paper_id == PAPER_ID
    assert len(pool.conn.calls) == 2


def test_same_key_with_different_payload_is_conflict_before_paper_access():
    current, changed = metadata(), metadata(title="Changed")
    pool = Pool([
        Result(),
        Result({"payload_hash": _payload_hash(current), "response_status": 201, "response_body": {}}),
    ])

    with pytest.raises(IdempotencyConflict):
        PostgresPaperStore(pool).register("user-1", "operation-key-0001", changed)

    assert len(pool.conn.calls) == 2


def test_payload_hash_normalizes_equivalent_timezone_offsets():
    first = metadata()
    same_in_korea = PaperMetadata(
        first.source_instance_id, first.local_paper_id, first.title, first.authors,
        first.publication_year, first.original_created_at.astimezone(timezone(timedelta(hours=9))),
    )

    assert _payload_hash(first) == _payload_hash(same_in_korea)


class MemoryStore:
    def __init__(self):
        self.calls = []

    def register(self, owner_user_id, key, item):
        self.calls.append((owner_user_id, key, item))
        return PaperRegistration(PAPER_ID, 1, "stored", NOW)


def endpoint(app):
    return next(route.endpoint for route in app.routes if route.path == "/v1/mcp/papers")


def payload():
    return SimpleNamespace(
        source_instance_id=UUID(SOURCE_ID), local_paper_id="local-paper-1", title=None,
        authors=None, publication_year=None, original_created_at=NOW,
    )


def test_route_uses_authenticated_device_owner_not_request_user_id():
    store = MemoryStore()
    route = endpoint(create_app(paper_store=store))
    principal = AuthenticatedPrincipal("user-1", "device", "00000000-0000-0000-0000-000000000001")

    response = route(payload(), principal, "operation-key-0001")

    assert response.paper_id == PAPER_ID
    assert store.calls[0][0:2] == ("user-1", "operation-key-0001")


def test_web_session_and_invalid_idempotency_key_are_rejected_before_store():
    store = MemoryStore()
    route = endpoint(create_app(paper_store=store))
    web = AuthenticatedPrincipal("user-1", "web_session")
    device = AuthenticatedPrincipal("user-1", "device", "00000000-0000-0000-0000-000000000001")

    with pytest.raises(HTTPException) as forbidden:
        route(payload(), web, "operation-key-0001")
    with pytest.raises(HTTPException) as invalid:
        route(payload(), device, "short")

    assert forbidden.value.status_code == 403
    assert invalid.value.status_code == 422
    assert store.calls == []


def test_openapi_marks_paper_registration_as_bearer_protected():
    operation = create_app().openapi()["paths"]["/v1/mcp/papers"]["post"]

    assert operation["security"] == [{"ServerAccessToken": []}]
    assert any(parameter["name"] == "Idempotency-Key" and parameter["required"] for parameter in operation["parameters"])

@pytest.mark.parametrize("change", [
    {"pdf_text": "private content"}, {"owner_user_id": "someone"},
    {"local_paper_id": "/private/paper.pdf"}, {"local_paper_id": r"C:\papers\one.pdf"},
    {"original_created_at": "0001-01-01T00:00:00+09:00"},
])
def test_rejected_paper_request_never_reaches_store_or_echoes_input(change):
    store = MemoryStore()
    principal = AuthenticatedPrincipal("user-1", "device", PAPER_ID)
    app = create_app(token_verifier=SimpleNamespace(verify=lambda _: principal),
                     device_registry=SimpleNamespace(is_active=lambda *_: True), paper_store=store)
    data = {"source_instance_id": SOURCE_ID, "local_paper_id": "local-paper", "original_created_at": NOW.isoformat()}
    with TestClient(app) as client:
        response = client.post("/v1/mcp/papers", json=data | change,
                               headers={"Authorization": "Bearer test", "Idempotency-Key": "operation-key-0001"})
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_paper_request"}}
    assert not store.calls
