"""외부 웹과 로컬 MCP 사이의 학습 이어하기 코드 API."""

import re
from typing import Annotated, Any
from uuid import UUID

from .auth import AuthenticatedPrincipal
from .resume_requests import (
    ResumeRequestConflict, ResumeRequestExpired, ResumeRequestNotFound,
    ResumeRequestStore, ResumeRequestStoreUnavailable,
)

_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_CODE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def create_resume_router(require_principal: Any, store: ResumeRequestStore):
    from fastapi import APIRouter, Depends, Header, HTTPException
    from pydantic import BaseModel, Field, field_validator

    class CreateInput(BaseModel):
        paper_id: UUID
        version_id: str = Field(min_length=1, max_length=128)
        learning_record_id: UUID
        annotation_id: UUID | None = None

        @field_validator("version_id")
        @classmethod
        def valid_version(cls, value):
            value = value.strip()
            if not value or "\x00" in value or "/" in value or "\\" in value:
                raise ValueError("invalid version")
            return value

    class ResolveInput(BaseModel):
        code: str

        @field_validator("code")
        @classmethod
        def valid_code(cls, value):
            value = value.strip()
            if not _CODE.fullmatch(value):
                raise ValueError("invalid code")
            return value

    router = APIRouter(tags=["resume"])
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    def run(action):
        try:
            return action()
        except ResumeRequestNotFound:
            raise HTTPException(status_code=404, detail={"code": "resume_target_not_found"}) from None
        except ResumeRequestExpired:
            raise HTTPException(status_code=410, detail={"code": "resume_request_expired"}) from None
        except ResumeRequestConflict:
            raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"}) from None
        except ResumeRequestStoreUnavailable:
            raise HTTPException(status_code=503, detail={"code": "resume_store_unavailable"}) from None

    @router.post("/v1/resume-requests")
    def create_request(body: CreateInput, principal: Principal,
                       idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None):
        if principal.credential_type != "web_session":
            raise HTTPException(status_code=403, detail={"code": "web_session_required"})
        if idempotency_key is None or not _KEY.fullmatch(idempotency_key):
            raise HTTPException(status_code=422, detail={"code": "invalid_idempotency_key"})
        return run(lambda: store.create(
            principal.user_id, idempotency_key, paper_id=str(body.paper_id),
            version_id=body.version_id, learning_record_id=str(body.learning_record_id),
            annotation_id=str(body.annotation_id) if body.annotation_id else None,
        ))

    @router.post("/v1/mcp/resume-requests/resolve")
    def resolve_request(body: ResolveInput, principal: Principal):
        if principal.credential_type != "device":
            raise HTTPException(status_code=403, detail={"code": "device_credential_required"})
        return run(lambda: store.resolve(principal.user_id, body.code))

    return router
