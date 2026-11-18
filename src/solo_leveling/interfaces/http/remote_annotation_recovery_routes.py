"""명시적으로 주입한 웹 세션 전용 메모 복구 경계. 새 기록은 생성하지 않는다."""

import re
from dataclasses import asdict
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from solo_leveling.domain.annotation import SegmentInput
from .auth import AuthenticatedPrincipal
from .remote_annotations import (
    RemoteAnnotationInput,
    RemoteAnnotationRecoveryAmbiguous,
    RemoteAnnotationReferenceError,
)


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    chunk_id: str = Field(min_length=1, max_length=128)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    quote_snapshot: str = Field(min_length=1, max_length=2000)
    target: Literal["original", "translation"] = "original"
    translation_revision_id: str | None = None


class RecoveryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["note", "highlight"]
    body: str = Field(max_length=5000)
    segments: list[Segment] = Field(default_factory=list, max_length=20)
    step: str | None = None
    creation_origin: Literal["user", "ai", "unknown"] = "unknown"

    def domain(self):
        return RemoteAnnotationInput(
            self.kind, self.body,
            tuple(SegmentInput(**item.model_dump()) for item in self.segments),
            self.step, self.creation_origin,
        )


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    annotation_id: UUID
    revision: int = Field(strict=True, gt=0)


class AnnotationRecoveryService:
    """owner는 인증에서, 안정적인 service는 서버 조립 시에만 받는다."""

    def __init__(self, store, service_id: str):
        if not isinstance(service_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", service_id):
            raise ValueError("annotation_service_invalid")
        self.store = store
        self.service_id = service_id

    def lookup(self, owner, context, key):
        return self.store.lookup_request(owner, self.service_id, context, key)

    def recover(self, owner, context, value):
        return self.store.recover_request(owner, self.service_id, context, value)

    def confirm(self, owner, context, key, annotation_id, revision):
        return self.store.confirm_request(owner, self.service_id, context, key, annotation_id, revision)


class SafeRecoveryRoute(APIRoute):
    """입력 검증 오류에도 메모·인용문을 되돌려 보내지 않는다."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, {"code": "annotation_input_invalid"},
                                    headers={"Cache-Control": "no-store"}) from None
        return safe


def create_annotation_recovery_router(require_principal, service):
    router = APIRouter(prefix="/v1/remote-annotations/contexts", route_class=SafeRecoveryRoute)
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    def call(principal, response, operation):
        headers = {"Cache-Control": "no-store"}
        response.headers.update(headers)
        if principal.credential_type != "web_session":
            raise HTTPException(403, {"code": "web_session_required"}, headers=headers)
        try:
            value = operation(principal.user_id)
        except RemoteAnnotationRecoveryAmbiguous:
            raise HTTPException(409, {"code": "annotation_recovery_ambiguous"}, headers=headers) from None
        except RemoteAnnotationReferenceError:
            raise HTTPException(404, {"code": "annotation_not_found"}, headers=headers) from None
        except ValueError:
            raise HTTPException(422, {"code": "annotation_input_invalid"}, headers=headers) from None
        except Exception:
            raise HTTPException(503, {"code": "annotation_unavailable"}, headers=headers) from None
        if value is None:
            raise HTTPException(404, {"code": "annotation_not_found"}, headers=headers)
        return value

    def payload(value):
        return {**asdict(value), "reused": True}

    @router.get("/{context_id}/requests/{request_key}")
    def lookup(context_id: UUID, request_key: str, principal: Principal, response: Response):
        return payload(call(principal, response,
                            lambda owner: service.lookup(owner, str(context_id), request_key)))

    @router.post("/{context_id}/recover")
    def recover(context_id: UUID, body: RecoveryInput, principal: Principal, response: Response):
        return payload(call(principal, response,
                            lambda owner: service.recover(owner, str(context_id), body.domain())))

    @router.post("/{context_id}/requests/{request_key}/confirm")
    def confirm(context_id: UUID, request_key: str, body: Confirmation,
                principal: Principal, response: Response):
        confirmed = call(principal, response, lambda owner: service.confirm(
            owner, str(context_id), request_key, str(body.annotation_id), body.revision))
        if not confirmed:
            raise HTTPException(409, {"code": "annotation_confirmation_conflict"},
                                headers={"Cache-Control": "no-store"})
        return {"confirmed": True, "context_id": str(context_id), "request_key": request_key,
                "annotation_id": str(body.annotation_id), "revision": body.revision}

    return router
