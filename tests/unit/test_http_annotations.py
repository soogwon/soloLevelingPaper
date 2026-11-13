"""서버 메모·하이라이트 조회 API의 사용자 격리와 페이지 계약."""

from contextlib import nullcontext
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
from solo_leveling.interfaces.http.papers import (
    AnnotationDateCount, AnnotationPage, PaperNotFound, PostgresPaperStore, StoredAnnotation,
)


NOW = datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc)
ANNOTATION_ID = "00000000-0000-0000-0000-000000000501"
PAPER_ID = "00000000-0000-0000-0000-000000000201"
RECORD_ID = "00000000-0000-0000-0000-000000000301"


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


def row():
    return {
        "annotation_id": ANNOTATION_ID, "paper_id": PAPER_ID, "paper_title": "Paper",
        "learning_record_id": RECORD_ID, "selected_version_id": "version-1",
        "kind": "highlight", "body": "important", "step": "deep_read",
        "segments": [{"chunk_id": "chunk-1", "parse_revision_id": "parse-1",
                      "target": "translation", "translation_revision_id": "translation-1",
                      "start_offset": 0, "end_offset": 2, "quote_snapshot": "문장"}],
        "original_created_at": NOW, "updated_at": NOW,
    }


def stored():
    item = row()
    return StoredAnnotation(
        item["annotation_id"], item["paper_id"], item["paper_title"],
        item["learning_record_id"], item["selected_version_id"], item["kind"], item["body"],
        item["step"], tuple(item["segments"]), item["original_created_at"], item["updated_at"],
    )


def test_postgres_list_uses_owner_scope_full_date_counts_and_stable_order():
    pool = Pool([
        Result(), Result({"total_count": 25}),
        Result(many=[{"created_date": date(2026, 11, 14), "item_count": 25}]),
        Result(many=[row()]),
    ])

    page = PostgresPaperStore(pool).list_annotations(
        "user-1", kind="highlight", start_date=date(2026, 11, 14),
        end_date=date(2026, 11, 14), query="문장", sort="newest", paper_id=PAPER_ID,
        version_id="version-1", page=2, limit=20,
    )

    assert page.total_count == 25 and page.total_pages == 2
    assert page.date_counts == (AnnotationDateCount(date(2026, 11, 14), 25),)
    count_sql, count_params = pool.conn.calls[1]
    assert "a.owner_user_id=%s" in count_sql
    assert count_params[0] == "user-1"
    assert "jsonb_array_elements(a.segments)" in count_sql
    list_sql = pool.conn.calls[3][0]
    assert "a.original_created_at DESC, a.annotation_id DESC" in list_sql


def test_page_past_end_keeps_counts_without_item_query():
    pool = Pool([
        Result(), Result({"total_count": 1}),
        Result(many=[{"created_date": date(2026, 11, 14), "item_count": 1}]),
    ])

    page = PostgresPaperStore(pool).list_annotations(
        "user-1", kind=None, start_date=None, end_date=None, query=None, sort="oldest",
        paper_id=None, version_id=None, page=3, limit=20,
    )

    assert page.items == () and page.page == 3 and page.total_pages == 1
    assert len(pool.conn.calls) == 3


def test_get_annotation_is_owner_scoped():
    pool = Pool([Result(), Result(row())])

    item = PostgresPaperStore(pool).get_annotation("user-1", ANNOTATION_ID)

    assert item.annotation_id == ANNOTATION_ID
    assert pool.conn.calls[1][1] == ("user-1", ANNOTATION_ID)


class Store:
    def list_annotations(self, owner_user_id, **filters):
        assert owner_user_id == "user-1"
        return AnnotationPage((stored(),), (AnnotationDateCount(date(2026, 11, 14), 1),),
                              filters["page"], filters["limit"], 1, 1)

    def get_annotation(self, owner_user_id, annotation_id):
        assert owner_user_id == "user-1"
        if annotation_id != ANNOTATION_ID:
            raise PaperNotFound
        return stored()


def routes(store):
    app = create_app(paper_store=store)
    listing = next(route.endpoint for route in app.routes if route.path == "/v1/annotations")
    detail = next(route.endpoint for route in app.routes
                  if route.path == "/v1/annotations/{annotation_id}")
    return listing, detail


def test_web_routes_serialize_translation_segment_as_unverified():
    listing, detail = routes(Store())
    principal = AuthenticatedPrincipal("user-1", "web_session", None)

    page = listing(principal, None, None, None, None, "newest", None, None, 1, 20)
    item = detail(UUID(ANNOTATION_ID), principal)

    assert page.total_count == 1 and page.date_counts == {date(2026, 11, 14): 1}
    assert item.segments[0].target == "translation"
    assert item.segments[0].translation_revision_id == "translation-1"
    assert item.segments[0].text_match_verified is False


def test_device_credentials_cannot_read_web_annotations():
    listing, _ = routes(Store())
    principal = AuthenticatedPrincipal("user-1", "device", "device-1")

    with pytest.raises(HTTPException) as error:
        listing(principal, None, None, None, None, "newest", None, None, 1, 20)

    assert error.value.status_code == 403


def test_invalid_date_range_is_rejected_before_store_access():
    listing, _ = routes(Store())
    principal = AuthenticatedPrincipal("user-1", "web_session", None)

    with pytest.raises(HTTPException) as error:
        listing(principal, None, date(2026, 11, 15), date(2026, 11, 14),
                None, "newest", None, None, 1, 20)

    assert error.value.status_code == 422
    assert error.value.detail == {"code": "invalid_annotation_date_range"}


def test_missing_annotation_returns_fixed_404_code():
    _, detail = routes(Store())
    principal = AuthenticatedPrincipal("user-1", "web_session", None)

    with pytest.raises(HTTPException) as error:
        detail(UUID("00000000-0000-0000-0000-000000000999"), principal)

    assert error.value.status_code == 404
    assert error.value.detail == {"code": "annotation_not_found"}


def test_web_list_query_and_card_fields():
    from fastapi.testclient import TestClient

    class CaptureStore(Store):
        def list_annotations(self, owner_user_id, **filters):
            assert filters["start_date"] == filters["end_date"] == date(2026, 11, 14)
            assert filters["sort"] == "newest"
            return super().list_annotations(owner_user_id, **filters)

    principal = AuthenticatedPrincipal("user-1", "web_session")
    app = create_app(paper_store=CaptureStore(), token_verifier=SimpleNamespace(verify=lambda _: principal))
    with TestClient(app) as client:
        result = client.get("/v1/annotations?from=2026-11-14&to=2026-11-14&sort=newest",
                            headers={"Authorization": "Bearer test"})
    assert result.status_code == 200
    page = result.json()
    assert page["date_counts"] == {"2026-11-14": 1}
    item, = page["items"]
    assert item["paper"] == {"paper_id": PAPER_ID, "title": "Paper"}
    assert item["version_id"] == "version-1"
    assert item["segments"][0]["quote"] == row()["segments"][0]["quote_snapshot"]
