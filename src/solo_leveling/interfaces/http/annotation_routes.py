"""로그인 사용자의 서버 동기화 메모·하이라이트 조회 API."""

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from .auth import AuthenticatedPrincipal
from .papers import PaperNotFound, PaperStore, PaperStoreUnavailable, StoredAnnotation


def create_annotation_router(require_principal: Any, store: PaperStore):
    from fastapi import APIRouter, Depends, HTTPException, Query, status
    from pydantic import BaseModel

    class SegmentView(BaseModel):
        chunk_id: str
        parse_revision_id: str
        target: Literal["original", "translation"]
        translation_revision_id: str | None
        start_offset: int
        end_offset: int
        quote_snapshot: str
        text_match_verified: bool = False

    class AnnotationView(BaseModel):
        annotation_id: UUID
        paper_id: UUID
        paper_title: str | None
        learning_record_id: UUID
        selected_version_id: str | None
        kind: Literal["note", "highlight"]
        body: str
        step: str | None
        segments: list[SegmentView]
        created_at: datetime
        updated_at: datetime

    class DateCountView(BaseModel):
        date: date
        count: int

    class AnnotationPageView(BaseModel):
        items: list[AnnotationView]
        date_counts: list[DateCountView]
        page: int
        page_size: int
        total_count: int
        total_pages: int

    router = APIRouter(prefix="/v1/annotations", tags=["web-annotations"])
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    def require_web(principal: AuthenticatedPrincipal) -> None:
        if principal.credential_type != "web_session":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail={"code": "web_session_required"})

    def view(item: StoredAnnotation) -> AnnotationView:
        segments = []
        for segment in item.segments:
            segments.append(SegmentView(
                chunk_id=segment["chunk_id"], parse_revision_id=segment["parse_revision_id"],
                target=segment["target"],
                translation_revision_id=segment.get("translation_revision_id"),
                start_offset=segment["start_offset"], end_offset=segment["end_offset"],
                quote_snapshot=segment["quote_snapshot"], text_match_verified=False,
            ))
        return AnnotationView(
            annotation_id=item.annotation_id, paper_id=item.paper_id,
            paper_title=item.paper_title, learning_record_id=item.learning_record_id,
            selected_version_id=item.selected_version_id, kind=item.kind, body=item.body,
            step=item.step, segments=segments, created_at=item.created_at,
            updated_at=item.updated_at,
        )

    @router.get("", response_model=AnnotationPageView)
    def list_annotations(
        principal: Principal,
        kind: Literal["note", "highlight"] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
        sort: Literal["recent", "oldest"] = "recent",
        paper_id: UUID | None = None,
        version_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        require_web(principal)
        if start_date is not None and end_date is not None and start_date > end_date:
            raise HTTPException(status_code=422, detail={"code": "invalid_annotation_date_range"})
        if end_date == date.max:
            raise HTTPException(status_code=422, detail={"code": "invalid_annotation_date_range"})
        normalized_query = q.strip() if q is not None else None
        if q is not None and (not normalized_query or "\x00" in q):
            raise HTTPException(status_code=422, detail={"code": "invalid_annotation_query"})
        normalized_version = version_id.strip() if version_id is not None else None
        if version_id is not None and (not normalized_version or "\x00" in version_id
                                       or "/" in version_id or "\\" in version_id):
            raise HTTPException(status_code=422, detail={"code": "invalid_version_id"})
        try:
            result = store.list_annotations(
                principal.user_id, kind=kind, start_date=start_date, end_date=end_date,
                query=normalized_query, sort=sort,
                paper_id=str(paper_id) if paper_id is not None else None,
                version_id=normalized_version, page=page, limit=limit,
            )
        except PaperStoreUnavailable:
            raise HTTPException(status_code=503,
                                detail={"code": "annotation_store_unavailable"}) from None
        return AnnotationPageView(
            items=[view(item) for item in result.items],
            date_counts=[DateCountView(date=item.date, count=item.count)
                         for item in result.date_counts],
            page=result.page, page_size=result.page_size,
            total_count=result.total_count, total_pages=result.total_pages,
        )

    @router.get("/{annotation_id}", response_model=AnnotationView)
    def get_annotation(annotation_id: UUID, principal: Principal):
        require_web(principal)
        try:
            return view(store.get_annotation(principal.user_id, str(annotation_id)))
        except PaperNotFound:
            raise HTTPException(status_code=404, detail={"code": "annotation_not_found"}) from None
        except PaperStoreUnavailable:
            raise HTTPException(status_code=503,
                                detail={"code": "annotation_store_unavailable"}) from None

    return router
