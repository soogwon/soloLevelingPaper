"""PDF 업로드 준비 HTTP 계약."""

from pathlib import PureWindowsPath
import re
from typing import Annotated, Any
from uuid import UUID

from .auth import AuthenticatedPrincipal
from .file_uploads import (
    MAX_FILE_BYTES, FileUploadStore, UploadAlreadyReady, UploadQuotaExceeded, UploadTargetNotFound,
    UploadUnavailable,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def create_file_router(require_principal: Any, store: FileUploadStore, object_storage: Any):
    from fastapi import APIRouter, Depends, HTTPException, status
    from pydantic import BaseModel, ConfigDict, Field, field_validator

    class UploadPrepare(BaseModel):
        model_config = ConfigDict(extra="forbid")
        server_version_id: str = Field(min_length=1, max_length=128)
        local_version_id: str = Field(min_length=1, max_length=128)
        file_name: str = Field(min_length=1, max_length=500)
        file_size: int = Field(strict=True, gt=0, le=MAX_FILE_BYTES)
        content_type: str
        sha256: str
        consent: bool

        @field_validator("server_version_id", "local_version_id", "file_name")
        @classmethod
        def safe_text(cls, value):
            value = value.strip()
            if not value or "\x00" in value or "/" in value or "\\" in value or PureWindowsPath(value).drive:
                raise ValueError("경로를 사용할 수 없습니다.")
            return value

        @field_validator("content_type")
        @classmethod
        def pdf_only(cls, value):
            if value.strip().lower() != "application/pdf":
                raise ValueError("PDF만 업로드할 수 있습니다.")
            return "application/pdf"

        @field_validator("sha256")
        @classmethod
        def valid_sha256(cls, value):
            value = value.strip().lower()
            if not _SHA256.fullmatch(value):
                raise ValueError("SHA-256 형식이 올바르지 않습니다.")
            return value

    class UploadPrepareView(BaseModel):
        upload_id: UUID
        upload_url: str
        method: str
        headers: dict[str, str]
        expires_at: str
        max_file_size: int

    router = APIRouter(prefix="/v1/papers", tags=["paper-files"])
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    @router.post("/{paper_id}/uploads", response_model=UploadPrepareView,
                 status_code=status.HTTP_201_CREATED)
    def prepare_upload(paper_id: UUID, payload: UploadPrepare, principal: Principal):
        if principal.credential_type != "device":
            raise HTTPException(status_code=403, detail={"code": "device_credential_required"})
        if not payload.consent:
            raise HTTPException(status_code=422, detail={"code": "pdf_upload_consent_required"})
        try:
            reservation = store.reserve(
                principal.user_id, str(paper_id), payload.server_version_id,
                payload.local_version_id, payload.file_name, payload.file_size,
                payload.content_type, payload.sha256,
            )
            prepared = object_storage.prepare_upload(
                reservation, content_type=payload.content_type, sha256_hex=payload.sha256)
        except UploadTargetNotFound:
            raise HTTPException(status_code=404, detail={"code": "paper_version_not_found"}) from None
        except UploadQuotaExceeded:
            raise HTTPException(status_code=413, detail={"code": "pdf_storage_quota_exceeded"}) from None
        except UploadAlreadyReady:
            raise HTTPException(status_code=409, detail={"code": "pdf_already_uploaded"}) from None
        except (UploadUnavailable, RuntimeError):
            raise HTTPException(status_code=503, detail={"code": "pdf_upload_unavailable"}) from None
        return UploadPrepareView(
            upload_id=UUID(reservation.upload_id), upload_url=prepared.url,
            method=prepared.method, headers=prepared.headers,
            expires_at=prepared.expires_at.isoformat(), max_file_size=MAX_FILE_BYTES,
        )

    return router
