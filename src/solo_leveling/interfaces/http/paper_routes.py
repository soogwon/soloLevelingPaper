"""로컬 기기의 논문 메타데이터 동기화 API."""

from datetime import datetime
import re
from typing import Annotated, Any
from uuid import UUID

from .auth import AuthenticatedPrincipal
from .papers import IdempotencyConflict, PaperMetadata, PaperStore, PaperStoreUnavailable


_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")


def create_paper_router(require_principal: Any, store: PaperStore):
    from fastapi import APIRouter, Depends, Header, HTTPException, status
    from pydantic import BaseModel, Field, field_validator

    class PaperCreate(BaseModel):
        source_instance_id: UUID
        local_paper_id: str = Field(min_length=1, max_length=128)
        title: str | None = Field(default=None, max_length=1000)
        authors: list[str] | None = Field(default=None, max_length=100)
        publication_year: int | None = Field(default=None, ge=1000, le=2100)
        original_created_at: datetime

        @field_validator("local_paper_id")
        @classmethod
        def validate_local_id(cls, value):
            if "\x00" in value or not value.strip():
                raise ValueError("local_paper_id가 올바르지 않습니다.")
            return value.strip()

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
            return value

    class PaperView(BaseModel):
        paper_id: str
        revision: int
        sync_status: str
        server_updated_at: datetime

    router = APIRouter(prefix="/v1/mcp/papers", tags=["paper-sync"])
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
