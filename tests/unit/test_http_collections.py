"""컬렉션 API의 웹 세션 범위와 저장 계약을 검증한다."""

from contextlib import nullcontext
from datetime import datetime, timezone
from inspect import signature

from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
from solo_leveling.interfaces.http.collection_routes import _normalize_name
from solo_leveling.interfaces.http.collections import (
    CollectionItem, CollectionMembership, CollectionMembershipConflict, CollectionPage,
    CollectionPaperItem, CollectionPaperPage,
    PostgresCollectionStore,
)

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)
COLLECTION_ID = "00000000-0000-0000-0000-000000000301"
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
        self.results, self.calls = list(results), []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return self.results.pop(0)


class Pool:
    def __init__(self, results):
        self.conn = Connection(results)

    def connection(self):
        return nullcontext(self.conn)


class Store:
    def __init__(self):
        self.calls = []

    def list_collections(self, owner, *, page, limit):
        self.calls.append((owner, page, limit))
        item = CollectionItem(COLLECTION_ID, "Research", 2, NOW, NOW)
        return CollectionPage((item,), page, limit, 1, 1)

    def create_collection(self, owner, key, name, normalized):
        self.calls.append((owner, key, name, normalized))
        return CollectionItem(COLLECTION_ID, name, 0, NOW, NOW)

    def get_collection(self, owner, collection_id):
        return CollectionItem(collection_id, "Research", 0, NOW, NOW)

    def rename_collection(self, owner, collection_id, name, normalized):
        return CollectionItem(collection_id, name, 0, NOW, NOW)

    def delete_collection(self, owner, collection_id):
        return None

    def list_papers(self, owner, collection_id, *, page, limit):
        return CollectionPaperPage((), page, limit, 0, 0)

    def add_paper(self, owner, collection_id, paper_id):
        return CollectionMembership(collection_id, paper_id, 1)

    def remove_paper(self, owner, collection_id, paper_id):
        return CollectionMembership(collection_id, paper_id, 0)


def endpoint(store, path, method):
    app = create_app(collection_store=store)
    return next(route.endpoint for route in app.routes
                if getattr(route, "path", None) == path and method in getattr(route, "methods", ()))


def test_list_is_scoped_to_authenticated_web_user():
    store = Store()
    response = endpoint(store, "/v1/collections", "GET")(
        AuthenticatedPrincipal("user-1", "web_session"), 2, 5)
    assert store.calls == [("user-1", 2, 5)]
    assert response.items[0].collection_id == COLLECTION_ID


def test_create_normalizes_unicode_whitespace_and_casefolds_name():
    store = Store()
    route = endpoint(store, "/v1/collections", "POST")
    body_type = signature(route).parameters["body"].annotation
    route(body_type(name="  Ｒesearch  "), AuthenticatedPrincipal("user-1", "web_session"),
          "collection-create-0001")
    assert store.calls[-1] == ("user-1", "collection-create-0001", "Ｒesearch", "ｒesearch")


def test_device_token_cannot_use_external_collection_api():
    import pytest
    from fastapi import HTTPException
    route = endpoint(Store(), "/v1/collections", "GET")
    with pytest.raises(HTTPException) as exc:
        route(AuthenticatedPrincipal("user-1", "device", "device-1"), 1, 20)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "web_session_required"


def test_name_normalization_uses_nfc():
    assert _normalize_name("  Cafe\u0301  ") == ("Café", "café")


def test_collection_paper_response_matches_external_site_published_shape():
    class PaperStore(Store):
        def list_papers(self, owner, collection_id, *, page, limit):
            item = CollectionPaperItem(PAPER_ID, "Paper", "paper.pdf", ("Author",), 2024, NOW)
            return CollectionPaperPage((item,), page, limit, 1, 1)

    route = endpoint(PaperStore(), "/v1/collections/{collection_id}/papers", "GET")
    result = route(COLLECTION_ID, AuthenticatedPrincipal("user-1", "web_session"), 1, 20)
    assert result.items[0].published.model_dump() == {
        "year": 2024, "month": None, "day": None, "precision": "year"
    }


def test_add_paper_creates_membership_and_counts_target_collection():
    pool = Pool([
        Result({"collection_id": COLLECTION_ID, "paper_id": PAPER_ID}),
        Result({"paper_count": 3}),
    ])
    result = PostgresCollectionStore(pool).add_paper("user-1", COLLECTION_ID, PAPER_ID)
    sql, params = pool.conn.calls[0]
    assert "ON CONFLICT(paper_id) DO NOTHING" in sql
    assert params == ("user-1", PAPER_ID, COLLECTION_ID)
    assert result == CollectionMembership(COLLECTION_ID, PAPER_ID, 3)


def test_add_paper_rejects_membership_in_another_collection():
    other = "00000000-0000-0000-0000-000000000302"
    pool = Pool([
        Result(), Result({"collection_id": other, "paper_id": PAPER_ID}),
    ])
    import pytest
    with pytest.raises(CollectionMembershipConflict):
        PostgresCollectionStore(pool).add_paper("user-1", COLLECTION_ID, PAPER_ID)


def test_collection_listing_uses_owner_scope_and_stable_pagination():
    pool = Pool([
        Result(), Result({"total_count": 1}),
        Result(many=[{"collection_id": COLLECTION_ID, "name": "Research",
                      "paper_count": 0, "created_at": NOW, "updated_at": NOW}]),
    ])
    result = PostgresCollectionStore(pool).list_collections("user-1", page=1, limit=20)
    assert result.total_count == 1
    assert pool.conn.calls[1][1] == ("user-1",)
    assert "ORDER BY c.created_at DESC, c.collection_id DESC" in pool.conn.calls[2][0]
