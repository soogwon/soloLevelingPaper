"""명시적 서버 논문 삭제 접수와 기존 요청 조회. 물리 정리는 worker가 맡는다."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import AuthenticatedPrincipal


NOTICE_VERSION = "server-paper-deletion-v1"


class DeletionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notice_version: Literal["server-paper-deletion-v1"]
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("explicit_confirmation_required")
        return value


class DeletionOutput(BaseModel):
    request_id: UUID
    paper_id: UUID
    status: Literal["blocking", "cleaning", "failed", "complete"]
    backup_erasure_confirmed: Literal[False] = False


def create_deletion_router(require_principal, store):
    router = APIRouter(prefix="/v1/papers", tags=["deletion"])
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]
    Key = Annotated[str, Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]

    def invoke(principal, paper_id, key, *, submit=False):
        if store is None:
            raise HTTPException(503, detail={"code": "deletion_unavailable"})
        try:
            result = (store.request if submit else store.get_existing)(principal.user_id, str(paper_id), key)
            if result is None:
                raise HTTPException(404, detail={"code": "deletion_not_found"})
            if result.paper_id != str(paper_id):
                raise HTTPException(409, detail={"code": "deletion_request_conflict"})
            # 다른 키에 기존 요청을 암묵적으로 연결하지 않는다.
            if submit and store.get_existing(principal.user_id, str(paper_id), key) is None:
                raise HTTPException(409, detail={"code": "deletion_request_conflict"})
            return DeletionOutput(request_id=result.request_id, paper_id=result.paper_id, status=result.status)
        except HTTPException:
            raise
        except ValueError as exc:
            if str(exc) == "remote_deletion_request_conflict":
                raise HTTPException(409, detail={"code": "deletion_request_conflict"}) from None
            raise HTTPException(503, detail={"code": "deletion_unavailable"}) from None
        except Exception:
            raise HTTPException(503, detail={"code": "deletion_unavailable"}) from None

    @router.get("/{paper_id}/deletion-requests/{request_key}", response_model=DeletionOutput)
    def get_existing(paper_id: UUID, request_key: Key, principal: Principal):
        return invoke(principal, paper_id, request_key)

    @router.put("/{paper_id}/deletion-requests/{request_key}", response_model=DeletionOutput)
    def request_deletion(paper_id: UUID, request_key: Key, payload: DeletionInput, principal: Principal):
        return invoke(principal, paper_id, request_key, submit=True)

    return router
