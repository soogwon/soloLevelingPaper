"""로컬 기기의 논문 메타데이터 동기화 API."""

from datetime import datetime, timezone
from pathlib import PureWindowsPath
import re
from typing import Annotated, Any, Literal
from uuid import UUID

from .auth import AuthenticatedPrincipal
from .papers import IdempotencyConflict, PaperMetadata, PaperStore, PaperStoreUnavailable


_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")


def create_paper_router(require_principal: Any, store: PaperStore):
    from fastapi import APIRouter, Depends, Header, HTTPException, status
    from fastapi.exceptions import RequestValidationError
    from fastapi.routing import APIRoute
    from pydantic import BaseModel, ConfigDict, Field, field_validator

    class PaperCreate(BaseModel):
        model_config = ConfigDict(extra="forbid")

        source_instance_id: UUID
        local_paper_id: str = Field(min_length=1, max_length=128)
        title: str | None = Field(default=None, max_length=1000)
        authors: list[str] | None = Field(default=None, max_length=100)
        publication_year: int | None = Field(default=None, ge=1000, le=2100)
        original_created_at: datetime

        @field_validator("local_paper_id")
        @classmethod
        def validate_local_id(cls, value):
            value = value.strip()
            if not value or "\x00" in value or "/" in value or "\\" in value or PureWindowsPath(value).drive:
                raise ValueError("local_paper_id가 올바르지 않습니다.")
            return value

        @field_validator("title")
        @classmethod
        def normalize_title(cls, value):
            if value is None:
                return None
            if "\x00" in value:
                raise ValueError("title에 NUL 문자를 사용할 수 없습니다.")
            return value.strip() or None

        @field_validator("authors")
        @classmethod
        def normalize_authors(cls, value):
            if value is None:
                return None
            normalized = []
            for author in value:
                if not isinstance(author, str) or "\x00" in author or not author.strip() or len(author) > 300:
                    raise ValueError("authors 항목이 올바르지 않습니다.")
                normalized.append(author.strip())
            return normalized or None

        @field_validator("original_created_at")
        @classmethod
        def require_timezone(cls, value):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("original_created_at에는 시간대가 필요합니다.")
            try:
                return value.astimezone(timezone.utc)
            except OverflowError:
                raise ValueError("UTC로 변환할 수 없는 등록 시각입니다.") from None

    class PaperView(BaseModel):
        paper_id: str
        revision: int
        sync_status: str
        server_updated_at: datetime

    class PaperRoute(APIRoute):
        def get_route_handler(self):
            handle = super().get_route_handler()

            async def register_request(request):
                try:
                    return await handle(request)
                except RequestValidationError:
                    # 거부된 본문에는 로컬 경로나 원문이 있을 수 있다.
                    raise HTTPException(status_code=422, detail={"code": "invalid_paper_request"}) from None

            return register_request

    router = APIRouter(prefix="/v1/mcp/papers", tags=["paper-sync"], route_class=PaperRoute)
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    @router.post("", response_model=PaperView, status_code=status.HTTP_201_CREATED)
    def register(
        payload: PaperCreate,
        principal: Principal,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ):
        if principal.credential_type != "device":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "device_credential_required"})
        if not _KEY.fullmatch(idempotency_key):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail={"code": "invalid_idempotency_key"})
        metadata = PaperMetadata(
            str(payload.source_instance_id), payload.local_paper_id, payload.title,
            tuple(payload.authors) if payload.authors is not None else None,
            payload.publication_year, payload.original_created_at,
        )
        try:
            return PaperView(**store.register(principal.user_id, idempotency_key, metadata).__dict__)
        except IdempotencyConflict:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail={"code": "idempotency_conflict"}) from None
        except PaperStoreUnavailable:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail={"code": "paper_store_unavailable"}) from None

    return router


def create_web_paper_router(require_principal: Any, store: PaperStore):
    """외부 웹이 로그인 사용자의 서버 확정 논문만 페이지 단위로 조회한다."""
    from fastapi import APIRouter, Depends, HTTPException, Query, status
    from pydantic import BaseModel

    class PublishedView(BaseModel):
        year: int
        month: None = None
        day: None = None
        precision: Literal["year"] = "year"

    class PaperListItemView(BaseModel):
        paper_id: str
        title: str | None
        authors: list[str] | None
        file_name: str | None
        published: PublishedView | None
        selected_learning_record_id: str | None
        selected_version_id: str | None
        last_position: dict[str, Any] | None
        last_studied_at: datetime | None
        reading_status: Literal["not_started", "reading", "completed", "unknown"]
        collection_id: str | None

    class PaperPageView(BaseModel):
        items: list[PaperListItemView]
        page: int
        page_size: int
        total_count: int
        total_pages: int

    router = APIRouter(prefix="/v1/papers", tags=["web-papers"])
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    @router.get("", response_model=PaperPageView)
    def list_papers(
        principal: Principal,
        q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
        reading_status: Literal["not_started", "reading", "completed", "unknown"] | None = None,
        sort: Literal["recent"] = "recent",
        page: Annotated[int, Query(ge=1)] = 1,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        if principal.credential_type != "web_session":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail={"code": "web_session_required"})
        normalized_query = q.strip() if q is not None else None
        if q is not None and not normalized_query:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail={"code": "invalid_paper_query"})
        try:
            result = store.list_papers(
                principal.user_id, query=normalized_query, reading_status=reading_status,
                page=page, limit=limit,
            )
        except PaperStoreUnavailable:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail={"code": "paper_store_unavailable"}) from None
        return PaperPageView(
            items=[PaperListItemView(
                paper_id=item.paper_id, title=item.title, authors=list(item.authors) if item.authors else None,
                file_name=None,
                published=({"year": item.publication_year, "month": None, "day": None, "precision": "year"}
                           if item.publication_year is not None else None),
                selected_learning_record_id=item.selected_learning_record_id,
                selected_version_id=item.selected_version_id, last_position=item.last_position,
                last_studied_at=item.last_studied_at, reading_status=item.reading_status,
                collection_id=item.collection_id,
            ) for item in result.items],
            page=result.page, page_size=result.page_size,
            total_count=result.total_count, total_pages=result.total_pages,
        )

    return router
