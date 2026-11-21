"""인증된 웹의 원격 PDF 보관·AI 전송 동의와 처리 접수 API."""

from typing import Annotated, Any, Literal
from uuid import UUID

from .auth import AuthenticatedPrincipal
from .remote_processing_intake import RemoteProcessingConflict, RemoteProcessingQuotaExceeded
from .remote_upload_consents import (
    AI_NOTICE,
    NOTICE_VERSION,
    STORAGE_NOTICE,
    RemoteUploadConsentConflict,
)


def create_remote_upload_consent_router(require_principal: Any, consents: Any, processing: Any):
    from fastapi import APIRouter, Depends, Header, HTTPException, Response
    from pydantic import BaseModel, ConfigDict, Field

    class ConsentView(BaseModel):
        consent_id: UUID
        purpose: Literal["storage", "ai_provider"]
        state: Literal["current", "stale", "revoked"]
        revision: int
        notice_version: str
        granted_at: str
        revoked_at: str | None

    class ConsentStatusView(BaseModel):
        intent_id: UUID
        notice_version: str
        storage_notice: str
        ai_provider_notice: str
        storage: ConsentView | None
        ai_provider: ConsentView | None

    class GrantRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        notice_version: str = Field(min_length=1, max_length=100)

    class RevokeRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        expected_revision: int = Field(strict=True, ge=1)

    class ProcessingRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        storage_consent_id: UUID
        ai_consent_id: UUID

    class ProcessingView(BaseModel):
        job_id: UUID
        paper_id: UUID
        version_id: UUID
        status: str

    router = APIRouter(prefix="/v1/remote-uploads/intents", tags=["remote-pdf-consent"])
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    def require_web(principal):
        if principal.credential_type != "web_session":
            raise HTTPException(status_code=403, detail={"code": "web_session_required"})

    class ProcessingSourceView(BaseModel):
        intent_id: UUID
        paper_id: UUID
        version_id: UUID
        active_publication_id: UUID | None

    @router.get("/by-paper/{paper_id}/{version_id}", response_model=ProcessingSourceView)
    def get_processing_source(
        paper_id: UUID, version_id: UUID, principal: Principal, response: Response,
    ):
        require_web(principal)
        response.headers["Cache-Control"] = "no-store"
        try:
            source = processing.find_source(principal.user_id, str(paper_id), str(version_id))
        except Exception:
            raise HTTPException(status_code=503, detail={"code": "remote_processing_unavailable"}) from None
        if source is None:
            raise _not_found(HTTPException)
        return ProcessingSourceView(paper_id=paper_id, version_id=version_id, **source)

    @router.get("/{intent_id}/consents", response_model=ConsentStatusView)
    def get_consents(intent_id: UUID, principal: Principal, response: Response):
        require_web(principal)
        response.headers["Cache-Control"] = "no-store"
        try:
            values = consents.list(principal.user_id, str(intent_id))
        except Exception:
            raise _unavailable(HTTPException) from None
        if values is None:
            raise _not_found(HTTPException)
        mapped = {item.purpose: _consent_view(ConsentView, item) for item in values}
        return ConsentStatusView(
            intent_id=intent_id, notice_version=NOTICE_VERSION,
            storage_notice=STORAGE_NOTICE, ai_provider_notice=AI_NOTICE,
            storage=mapped.get("storage"), ai_provider=mapped.get("ai_provider"),
        )

    @router.post("/{intent_id}/consents/{purpose}", response_model=ConsentView)
    def grant_consent(
        intent_id: UUID,
        purpose: Literal["storage", "ai_provider"],
        payload: GrantRequest,
        principal: Principal,
        response: Response,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    ):
        require_web(principal)
        response.headers["Cache-Control"] = "no-store"
        # 사용자가 확인한 고지가 바뀌었으면 새 고지 조회와 재동의를 요구한다.
        if payload.notice_version != NOTICE_VERSION:
            raise HTTPException(
                status_code=409, detail={"code": "remote_upload_consent_notice_changed"})
        try:
            value = consents.grant(
                principal.user_id, str(intent_id), purpose, idempotency_key, NOTICE_VERSION)
        except RemoteUploadConsentConflict as exc:
            raise HTTPException(status_code=409, detail={"code": str(exc)}) from None
        except ValueError as exc:
            code = str(exc)
            status = 409 if code == "remote_upload_not_verified" else 422
            raise HTTPException(status_code=status, detail={"code": code}) from None
        except Exception:
            raise _unavailable(HTTPException) from None
        if value is None:
            raise _not_found(HTTPException)
        return _consent_view(ConsentView, value)

    @router.post("/{intent_id}/consents/{purpose}/revoke", response_model=ConsentView)
    def revoke_consent(
        intent_id: UUID,
        purpose: Literal["storage", "ai_provider"],
        payload: RevokeRequest,
        principal: Principal,
        response: Response,
    ):
        require_web(principal)
        response.headers["Cache-Control"] = "no-store"
        try:
            value = consents.revoke(
                principal.user_id, str(intent_id), purpose, payload.expected_revision)
        except RemoteUploadConsentConflict as exc:
            raise HTTPException(status_code=409, detail={"code": str(exc)}) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": str(exc)}) from None
        except Exception:
            raise _unavailable(HTTPException) from None
        if value is None:
            raise _not_found(HTTPException)
        return _consent_view(ConsentView, value)

    @router.post("/{intent_id}/processing", response_model=ProcessingView)
    def admit_processing(
        intent_id: UUID,
        payload: ProcessingRequest,
        principal: Principal,
        response: Response,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    ):
        require_web(principal)
        response.headers["Cache-Control"] = "no-store"
        try:
            value = processing.admit(
                principal.user_id, str(intent_id), idempotency_key,
                str(payload.storage_consent_id), str(payload.ai_consent_id), NOTICE_VERSION)
        except RemoteProcessingConflict:
            raise HTTPException(
                status_code=409, detail={"code": "remote_processing_request_conflict"}) from None
        except RemoteProcessingQuotaExceeded:
            raise _quota_exceeded(HTTPException) from None
        except ValueError:
            raise HTTPException(
                status_code=422, detail={"code": "remote_processing_input_invalid"}) from None
        except Exception:
            raise HTTPException(
                status_code=503, detail={"code": "remote_processing_unavailable"}) from None
        if value is None:
            raise HTTPException(
                status_code=409, detail={"code": "remote_processing_not_ready"})
        return _processing_view(ProcessingView, value)

    @router.get("/{intent_id}/processing", response_model=ProcessingView)
    def get_processing(intent_id: UUID, principal: Principal, response: Response):
        require_web(principal)
        response.headers["Cache-Control"] = "no-store"
        try:
            value = processing.get(principal.user_id, str(intent_id))
        except Exception:
            raise HTTPException(
                status_code=503, detail={"code": "remote_processing_unavailable"}) from None
        if value is None:
            if _quota_reached(processing, principal.user_id):
                raise _quota_exceeded(HTTPException)
            raise HTTPException(status_code=404, detail={"code": "remote_processing_not_found"})
        return _processing_view(ProcessingView, value)

    return router


def _quota_exceeded(http_exception):
    return http_exception(status_code=409, detail={"code": "remote_processing_quota_exceeded"})


def _quota_reached(processing, owner_user_id):
    """작업이 없을 때 한도 때문인지 알려 준다. 확인 실패는 기존 404 안내를 유지한다."""
    check = getattr(processing, "quota_reached", None)
    if check is None:
        return False
    try:
        return bool(check(owner_user_id))
    except Exception:
        return False


def _consent_view(view_type, value):
    state = ("revoked" if value.revoked_at is not None else
             "stale" if value.notice_version != NOTICE_VERSION else "current")
    return view_type(
        consent_id=UUID(value.consent_id), purpose=value.purpose,
        state=state,
        revision=value.revision, notice_version=value.notice_version,
        granted_at=value.granted_at.isoformat(),
        revoked_at=value.revoked_at.isoformat() if value.revoked_at is not None else None,
    )


def _processing_view(view_type, value):
    return view_type(
        job_id=UUID(value.job_id), paper_id=UUID(value.paper_id),
        version_id=UUID(value.server_version_id), status=value.status,
    )


def _not_found(exception_type):
    return exception_type(status_code=404, detail={"code": "remote_upload_not_found"})


def _unavailable(exception_type):
    return exception_type(status_code=503, detail={"code": "remote_upload_consent_unavailable"})
