"""외부 웹의 사용자 소유 컬렉션 API."""

from datetime import datetime
import re
import unicodedata
from typing import Annotated, Any
from uuid import UUID

from .auth import AuthenticatedPrincipal
from .collections import (
    CollectionIdempotencyConflict, CollectionMembershipConflict, CollectionNameConflict, CollectionNotFound,
    CollectionPaperNotFound, CollectionStore, CollectionStoreUnavailable,
)

_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")


def create_collection_router(require_principal: Any, store: CollectionStore):
    from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
    from pydantic import BaseModel

    class CollectionInput(BaseModel):
        name: str

    class CollectionView(BaseModel):
        collection_id: str
        name: str
        paper_count: int
        created_at: datetime
        updated_at: datetime

    class CollectionPageView(BaseModel):
        items: list[CollectionView]
        page: int
        page_size: int
        total_count: int
        total_pages: int

    class PublishedView(BaseModel):
        year: int
        month: None = None
        day: None = None
        precision: str = "year"

    class PaperView(BaseModel):
        paper_id: str
        title: str | None
        file_name: str | None
        authors: list[str] | None
        published: PublishedView | None
        added_at: datetime

    class PaperPageView(BaseModel):
        items: list[PaperView]
        page: int
        page_size: int
        total_count: int
        total_pages: int

    class MembershipView(BaseModel):
        collection_id: str
        paper_id: str
        paper_count: int

    router = APIRouter(prefix="/v1/collections", tags=["web-collections"])
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    def require_web(principal):
        if principal.credential_type != "web_session":
            raise HTTPException(status_code=403, detail={"code": "web_session_required"})

    def run(action):
        try:
            return action()
        except CollectionNotFound:
            raise HTTPException(status_code=404, detail={"code": "collection_not_found"}) from None
        except CollectionPaperNotFound:
            raise HTTPException(status_code=404, detail={"code": "collection_paper_not_found"}) from None
        except CollectionMembershipConflict:
            raise HTTPException(status_code=409, detail={"code": "collection_membership_conflict"}) from None
        except CollectionNameConflict:
            raise HTTPException(status_code=409, detail={"code": "collection_name_conflict"}) from None
        except CollectionIdempotencyConflict:
            raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"}) from None
        except CollectionStoreUnavailable:
            raise HTTPException(status_code=503, detail={"code": "collection_store_unavailable"}) from None

    @router.get("", response_model=CollectionPageView)
    def list_collections(principal: Principal, page: Annotated[int, Query(ge=1)] = 1,
                         limit: Annotated[int, Query(ge=1, le=100)] = 20):
        require_web(principal)
        result = run(lambda: store.list_collections(principal.user_id, page=page, limit=limit))
        return result

    @router.post("", response_model=CollectionView, status_code=status.HTTP_201_CREATED)
    def create_collection(body: CollectionInput, principal: Principal,
                          idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None):
        require_web(principal)
        if idempotency_key is None or not _KEY.fullmatch(idempotency_key):
            raise HTTPException(status_code=422, detail={"code": "invalid_idempotency_key"})
        name, normalized = _normalize_name(body.name)
        return run(lambda: store.create_collection(principal.user_id, idempotency_key, name, normalized))

    @router.get("/{collection_id}", response_model=CollectionView)
    def get_collection(collection_id: UUID, principal: Principal):
        require_web(principal)
        return run(lambda: store.get_collection(principal.user_id, str(collection_id)))

    @router.patch("/{collection_id}", response_model=CollectionView)
    def rename_collection(collection_id: UUID, body: CollectionInput, principal: Principal):
        require_web(principal)
        name, normalized = _normalize_name(body.name)
        return run(lambda: store.rename_collection(
            principal.user_id, str(collection_id), name, normalized))

    @router.delete("/{collection_id}")
    def delete_collection(collection_id: UUID, principal: Principal):
        require_web(principal)
        run(lambda: store.delete_collection(principal.user_id, str(collection_id)))
        return {"deleted": True, "collection_id": str(collection_id)}

    @router.get("/{collection_id}/papers", response_model=PaperPageView)
    def list_collection_papers(collection_id: UUID, principal: Principal,
                               page: Annotated[int, Query(ge=1)] = 1,
                               limit: Annotated[int, Query(ge=1, le=100)] = 20):
        require_web(principal)
        result = run(lambda: store.list_papers(
            principal.user_id, str(collection_id), page=page, limit=limit))
        return PaperPageView(items=[PaperView(
            paper_id=item.paper_id, title=item.title, file_name=item.file_name,
            authors=list(item.authors) if item.authors else None,
            published=({"year": item.publication_year, "month": None, "day": None,
                        "precision": "year"} if item.publication_year is not None else None),
            added_at=item.added_at,
        ) for item in result.items], page=result.page, page_size=result.page_size,
            total_count=result.total_count, total_pages=result.total_pages)

    @router.put("/{collection_id}/papers/{paper_id}", response_model=MembershipView)
    def add_paper(collection_id: UUID, paper_id: UUID, principal: Principal):
        require_web(principal)
        return run(lambda: store.add_paper(principal.user_id, str(collection_id), str(paper_id)))

    @router.delete("/{collection_id}/papers/{paper_id}", response_model=MembershipView)
    def remove_paper(collection_id: UUID, paper_id: UUID, principal: Principal):
        require_web(principal)
        return run(lambda: store.remove_paper(principal.user_id, str(collection_id), str(paper_id)))

    return router


def _normalize_name(value: str) -> tuple[str, str]:
    from fastapi import HTTPException
    name = unicodedata.normalize("NFC", value).strip()
    if not name or len(name) > 100 or "\x00" in name:
        raise HTTPException(status_code=422, detail={"code": "invalid_collection_name"})
    return name, name.casefold()
