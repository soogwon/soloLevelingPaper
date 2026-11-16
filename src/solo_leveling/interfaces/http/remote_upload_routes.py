"""인증된 웹 사용자의 신규 원격 PDF 업로드 HTTP 계약."""

from pathlib import PureWindowsPath
import re
from typing import Annotated, Any, Literal
from uuid import UUID

from .auth import AuthenticatedPrincipal
from .remote_uploads import RemoteUploadConflict


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def create_remote_upload_router(require_principal: Any, store: Any, verification: Any):
    from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
    from pydantic import BaseModel, ConfigDict, Field, field_validator

    class IntentCreate(BaseModel):
        model_config = ConfigDict(extra="forbid")
        file_name: str = Field(min_length=1, max_length=255)
        file_size: int = Field(strict=True, gt=0, le=50 * 1024 * 1024)
        content_type: Literal["application/pdf"]
        sha256: str

        @field_validator("file_name")
        @classmethod
        def safe_file_name(cls, value):
            value = value.strip()
            if (not value or "\x00" in value or "/" in value or "\\" in value
                    or PureWindowsPath(value).drive or any(ord(char) < 32 for char in value)):
                raise ValueError("파일 이름 형식이 올바르지 않습니다.")
            return value

        @field_validator("sha256")
        @classmethod
        def valid_sha256(cls, value):
            value = value.strip().lower()
            if not _SHA256.fullmatch(value):
                raise ValueError("SHA-256 형식이 올바르지 않습니다.")
            return value

    class IntentView(BaseModel):
        intent_id: UUID
        paper_id: UUID
        version_id: UUID
        status: Literal["created", "uploading", "verified", "failed", "expired"]
        expires_at: str

    class UploadPrepareView(BaseModel):
        intent_id: UUID
        upload_url: str
        method: Literal["PUT"]
        headers: dict[str, str]
        expires_at: str

    class UploadCompleteView(BaseModel):
        intent_id: UUID
        paper_id: UUID
        version_id: UUID
        status: Literal["verified"]
        file_name: str
        file_size: int
        content_type: Literal["application/pdf"]

    router = APIRouter(prefix="/v1/remote-uploads", tags=["remote-pdf-uploads"])
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    def require_web(principal):
        if principal.credential_type != "web_session":
            raise HTTPException(status_code=403, detail={"code": "web_session_required"})

    @router.post("/intents", response_model=IntentView, status_code=status.HTTP_201_CREATED)
    def create_intent(
        payload: IntentCreate,
        principal: Principal,
        response: Response,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    ):
        require_web(principal)
        response.headers["Cache-Control"] = "no-store"
        try:
            intent = store.create(
                principal.user_id, idempotency_key, payload.file_name, payload.file_size,
                payload.content_type, payload.sha256)
        except RemoteUploadConflict:
            raise HTTPException(
                status_code=409, detail={"code": "remote_upload_request_conflict"}) from None
        except ValueError:
            raise HTTPException(
                status_code=422, detail={"code": "remote_upload_input_invalid"}) from None
        except Exception:
            raise _unavailable(HTTPException) from None
        return _intent_view(IntentView, intent)

    @router.get("/intents/{intent_id}", response_model=IntentView)
    def get_intent(intent_id: UUID, principal: Principal, response: Response):
        require_web(principal)
        response.headers["Cache-Control"] = "no-store"
        try:
            intent = store.get(principal.user_id, str(intent_id))
        except Exception:
            raise _unavailable(HTTPException) from None
        if intent is None:
            raise HTTPException(status_code=404, detail={"code": "remote_upload_not_found"})
        return _intent_view(IntentView, intent)

    @router.post("/intents/{intent_id}/prepare", response_model=UploadPrepareView)
    def prepare_upload(intent_id: UUID, principal: Principal, response: Response):
        require_web(principal)
        response.headers["Cache-Control"] = "no-store"
        try:
            reservation, prepared = verification.prepare(principal.user_id, str(intent_id))
        except ValueError as exc:
            raise _known_error(HTTPException, exc) from None
        except Exception:
            raise _unavailable(HTTPException) from None
        return UploadPrepareView(
            intent_id=intent_id, upload_url=prepared.url, method=prepared.method,
            headers=prepared.headers, expires_at=reservation.expires_at.isoformat())

    @router.post("/intents/{intent_id}/complete", response_model=UploadCompleteView)
    def complete_upload(intent_id: UUID, principal: Principal, response: Response):
        require_web(principal)
        response.headers["Cache-Control"] = "no-store"
        try:
            completed = verification.complete(principal.user_id, str(intent_id))
        except ValueError as exc:
            raise _known_error(HTTPException, exc) from None
        except Exception:
            raise _unavailable(HTTPException) from None
        return UploadCompleteView(
            intent_id=intent_id, paper_id=UUID(completed.paper_id),
            version_id=UUID(completed.server_version_id), status="verified",
            file_name=completed.file_name, file_size=completed.file_size,
            content_type=completed.content_type)

    return router


def _intent_view(view_type, intent):
    return view_type(
        intent_id=UUID(intent.intent_id), paper_id=UUID(intent.paper_id),
        version_id=UUID(intent.server_version_id), status=intent.status,
        expires_at=intent.expires_at.isoformat())


def _known_error(exception_type, exc):
    code = str(exc)
    if code == "remote_upload_not_found":
        return exception_type(status_code=404, detail={"code": code})
    if code == "remote_upload_expired":
        return exception_type(status_code=410, detail={"code": code})
    if code == "remote_upload_state_invalid":
        return exception_type(status_code=409, detail={"code": code})
    if code == "remote_upload_object_invalid":
        return exception_type(status_code=422, detail={"code": code})
    return _unavailable(exception_type)


def _unavailable(exception_type):
    return exception_type(status_code=503, detail={"code": "remote_upload_unavailable"})
