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
    LearningRecordMetadata,
    LearningRecordRegistration,
    PaperListItem,
    PaperMetadata,
    PaperPage,
    PaperRegistration,
    PostgresPaperStore,
    _payload_hash,
)


NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
SOURCE_ID = "00000000-0000-0000-0000-000000000101"
PAPER_ID = "00000000-0000-0000-0000-000000000201"


class Result:
    def __init__(self, one=None, many=None):
        self.one, self.many = one, many or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class Connection:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def execute(self, sql, params=()):
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

    def list_papers(self, owner_user_id, **filters):
        self.calls.append((owner_user_id, filters))
        return PaperPage((), filters["page"], filters["limit"], 0, 0)

    def upsert_learning_record(self, owner_user_id, paper_id, item):
        self.calls.append((owner_user_id, paper_id, item))
        return LearningRecordRegistration(
            "00000000-0000-0000-0000-000000000301", 1, "stored", NOW
        )


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


def test_postgres_list_is_owner_scoped_searchable_and_stably_paginated():
    second = "00000000-0000-0000-0000-000000000202"
    pool = Pool([
        Result(), Result({"total_count": 22}),
        Result(many=[
            {"paper_id": PAPER_ID, "title": "Paper", "authors": ["A"], "publication_year": 2024},
            {"paper_id": second, "title": None, "authors": None, "publication_year": None},
        ]),
    ])

    result = PostgresPaperStore(pool).list_papers(
        "user-1", query=r" 100%_\\test ", reading_status=None, page=2, limit=20)

    assert result == PaperPage((
        PaperListItem(PAPER_ID, "Paper", ("A",), 2024),
        PaperListItem(second, None, None, None),
    ), 2, 20, 22, 2)
    count_sql, count_params = pool.conn.calls[1]
    page_sql, page_params = pool.conn.calls[2]
    assert "owner_user_id = %s" in count_sql and "title ILIKE %s" in count_sql
    assert count_params == ("user-1", r"% 100\%\_\\\\test %")
    assert "ORDER BY r.last_studied_at DESC NULLS LAST, p.paper_id ASC" in page_sql
    assert page_params == (*count_params, 20, 20)


def test_status_filter_uses_synced_record_and_excludes_unknown_papers():
    pool = Pool([Result(), Result({"total_count": 0})])

    result = PostgresPaperStore(pool).list_papers(
        "user-1", query=None, reading_status="reading", page=3, limit=10)

    assert result == PaperPage((), 3, 10, 0, 0)
    assert "COALESCE(r.reading_status, 'unknown') = %s" in pool.conn.calls[1][0]
    assert pool.conn.calls[1][1] == ("user-1", "reading")


def test_list_returns_latest_synced_learning_record_fields():
    record_id = "00000000-0000-0000-0000-000000000301"
    pool = Pool([
        Result(), Result({"total_count": 1}), Result(many=[{
            "paper_id": PAPER_ID, "title": "Paper", "authors": ["A"], "publication_year": 2024,
            "learning_record_id": record_id, "selected_version_id": "version-1",
            "file_name": "paper.pdf", "last_position": {"pdf_page": 3},
            "last_studied_at": NOW, "reading_status": "reading",
        }]),
    ])

    result = PostgresPaperStore(pool).list_papers(
        "user-1", query=None, reading_status="reading", page=1, limit=20)

    item = result.items[0]
    assert (item.selected_learning_record_id, item.selected_version_id, item.file_name) == (
        record_id, "version-1", "paper.pdf"
    )
    assert item.last_position == {"pdf_page": 3}
    assert (item.last_studied_at, item.reading_status) == (NOW, "reading")


def test_postgres_learning_record_upsert_is_owner_and_source_scoped():
    record_id = "00000000-0000-0000-0000-000000000301"
    pool = Pool([Result({"learning_record_id": record_id, "revision": 2, "updated_at": NOW})])
    metadata = LearningRecordMetadata(
        SOURCE_ID, "context-1", "version-1", "paper.pdf",
        {"pdf_page": 3, "section_title": "Method"}, NOW, "reading", NOW,
    )

    result = PostgresPaperStore(pool).upsert_learning_record("user-1", PAPER_ID, metadata)

    assert result == LearningRecordRegistration(record_id, 2, "stored", NOW)
    sql, params = pool.conn.calls[0]
    assert "p.owner_user_id=%s AND p.source_instance_id=%s" in sql
    assert "excluded.source_updated_at>=server_learning_records.source_updated_at" in sql
    assert params[-3:] == (PAPER_ID, "user-1", SOURCE_ID)


def test_learning_record_route_derives_status_and_uses_device_owner():
    store = MemoryStore()
    route = next(route.endpoint for route in create_app(paper_store=store).routes
                 if route.path == "/v1/mcp/papers/{paper_id}/learning-records/{local_record_id}")
    payload = SimpleNamespace(
        source_instance_id=UUID(SOURCE_ID), selected_version_id="version-1", file_name="paper.pdf",
        last_position=SimpleNamespace(model_dump=lambda **_: {"pdf_page": 3}),
        last_studied_at=NOW, completed_steps=list(("preview", "structure", "deep_read", "review")),
        source_updated_at=NOW,
    )
    principal = AuthenticatedPrincipal("user-1", "device", SOURCE_ID)

    result = route(UUID(PAPER_ID), "context-1", payload, principal)

    assert str(result.learning_record_id) == "00000000-0000-0000-0000-000000000301"
    assert store.calls[0][0:2] == ("user-1", PAPER_ID)
    assert store.calls[0][2].reading_status == "completed"


def test_web_list_uses_session_owner_and_preserves_out_of_range_page():
    store = MemoryStore()
    principal = AuthenticatedPrincipal("web-user", "web_session")
    app = create_app(token_verifier=SimpleNamespace(verify=lambda _: principal), paper_store=store)

    with TestClient(app) as client:
        response = client.get(
            "/v1/papers?q=%20Paper%20&page=7&limit=10",
            headers={"Authorization": "Bearer web-token"},
        )

    assert response.status_code == 200
    assert response.json() == {"items": [], "page": 7, "page_size": 10,
                               "total_count": 0, "total_pages": 0}
    assert store.calls == [("web-user", {"query": "Paper", "reading_status": None, "page": 7, "limit": 10})]


def test_web_list_shape_matches_external_site_without_inventing_learning_data():
    class ListStore(MemoryStore):
        def list_papers(self, owner_user_id, **filters):
            return PaperPage((PaperListItem(PAPER_ID, "Paper", ("A", "B"), 2024),), 1, 20, 1, 1)

    principal = AuthenticatedPrincipal("web-user", "web_session")
    app = create_app(token_verifier=SimpleNamespace(verify=lambda _: principal), paper_store=ListStore())
    with TestClient(app) as client:
        response = client.get("/v1/papers", headers={"Authorization": "Bearer web-token"})

    assert response.status_code == 200
    assert response.json()["items"] == [{
        "paper_id": PAPER_ID, "title": "Paper", "authors": ["A", "B"], "file_name": None,
        "published": {"year": 2024, "month": None, "day": None, "precision": "year"},
        "selected_learning_record_id": None, "selected_version_id": None,
        "last_position": None, "last_studied_at": None, "reading_status": "unknown",
        "collection_id": None,
    }]


def test_device_cannot_use_web_list_and_invalid_paging_never_reaches_store():
    store = MemoryStore()
    device = AuthenticatedPrincipal("user-1", "device", PAPER_ID)
    app = create_app(token_verifier=SimpleNamespace(verify=lambda _: device),
                     device_registry=SimpleNamespace(is_active=lambda *_: True), paper_store=store)
    with TestClient(app) as client:
        forbidden = client.get("/v1/papers", headers={"Authorization": "Bearer device"})
        invalid = client.get("/v1/papers?page=0&limit=101", headers={"Authorization": "Bearer device"})
    assert forbidden.status_code == 403
    assert forbidden.json() == {"detail": {"code": "web_session_required"}}
    assert invalid.status_code == 422
    assert store.calls == []


def test_openapi_exposes_protected_web_paper_page_contract():
    operation = create_app().openapi()["paths"]["/v1/papers"]["get"]
    assert operation["security"] == [{"ServerAccessToken": []}]
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert set(parameters) == {"q", "reading_status", "sort", "page", "limit"}
    assert parameters["page"]["schema"]["default"] == 1
    assert parameters["limit"]["schema"]["default"] == 20

def test_page_beyond_database_offset_range_preserves_count():
    pool = Pool([Result(), Result({"total_count": 10})])
    page = 10**20
    assert PostgresPaperStore(pool).list_papers("owner", query=None, reading_status=None, page=page, limit=20) == (
        PaperPage((), page, 20, 10, 1)
    )


def test_nul_in_search_query_is_rejected_before_database_access():
    store = MemoryStore()
    principal = AuthenticatedPrincipal("user", "web_session")
    app = create_app(token_verifier=SimpleNamespace(verify=lambda _: principal), paper_store=store)
    with TestClient(app) as client:
        response = client.get("/v1/papers?q=a%00b", headers={"Authorization": "Bearer web"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_paper_query"
    assert not store.calls
