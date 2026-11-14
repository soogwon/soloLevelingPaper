"""PDF 업로드 준비 HTTP 계약."""

from pathlib import PureWindowsPath
import re
from typing import Annotated, Any, Literal
from uuid import UUID

from .auth import AuthenticatedPrincipal
from .file_uploads import (
    MAX_FILE_BYTES, CompletedUpload, FileUploadStore, PendingUpload, UploadAlreadyReady,
    UploadExpired, UploadNotFound, UploadQuotaExceeded, UploadTargetNotFound,
    UploadReservationConflict, UploadUnavailable,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def create_file_router(require_principal: Any, store: FileUploadStore, object_storage: Any):
    from fastapi import APIRouter, Depends, HTTPException, Response, status
    from pydantic import BaseModel, ConfigDict, Field, field_validator

    class UploadPrepare(BaseModel):
        model_config = ConfigDict(extra="forbid")
        server_version_id: str = Field(min_length=1, max_length=128)
        local_version_id: str = Field(min_length=1, max_length=128)
        file_name: str = Field(min_length=1, max_length=500)
        file_size: int = Field(strict=True, gt=0, le=MAX_FILE_BYTES)
        content_type: str
        sha256: str
        consent: bool = Field(strict=True)

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

    class UploadCompleteView(BaseModel):
        upload_id: UUID
        paper_id: UUID
        version_id: str
        file_name: str
        file_size: int
        content_type: str
        storage_status: str
        failure_reason: None = None

    class FileAccessView(BaseModel):
        status: Literal["not_uploaded", "pending", "failed", "ready"]
        url: str | None = None
        expires_at: str | None = None
        file_name: str | None = None
        file_size: int | None = None
        content_type: str | None = None
        reason: str | None = None

    router = APIRouter(prefix="/v1", tags=["paper-files"])
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    @router.post("/papers/{paper_id}/uploads", response_model=UploadPrepareView,
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
                reservation, file_size=payload.file_size, content_type=payload.content_type, sha256_hex=payload.sha256)
        except UploadTargetNotFound:
            raise HTTPException(status_code=404, detail={"code": "paper_version_not_found"}) from None
        except UploadQuotaExceeded:
            raise HTTPException(status_code=413, detail={"code": "pdf_storage_quota_exceeded"}) from None
        except UploadReservationConflict:
            raise HTTPException(status_code=409, detail={"code": "pdf_upload_reservation_conflict"}) from None
        except UploadAlreadyReady:
            raise HTTPException(status_code=409, detail={"code": "pdf_already_uploaded"}) from None
        except (UploadUnavailable, RuntimeError):
            raise HTTPException(status_code=503, detail={"code": "pdf_upload_unavailable"}) from None
        return UploadPrepareView(
            upload_id=UUID(reservation.upload_id), upload_url=prepared.url,
            method=prepared.method, headers=prepared.headers,
            expires_at=prepared.expires_at.isoformat(), max_file_size=MAX_FILE_BYTES,
        )

    @router.post("/uploads/{upload_id}/complete", response_model=UploadCompleteView)
    def complete_upload(upload_id: UUID, principal: Principal):
        if principal.credential_type != "device":
            raise HTTPException(status_code=403, detail={"code": "device_credential_required"})
        try:
            upload = store.get_for_completion(principal.user_id, str(upload_id))
            if isinstance(upload, CompletedUpload):
                return _completed_view(UploadCompleteView, upload)
            actual = object_storage.inspect_upload(upload)
            if actual is None:
                raise HTTPException(status_code=409, detail={"code": "upload_object_missing"})
            failure = _verification_failure(upload, actual)
            if failure is not None:
                store.mark_failed(principal.user_id, str(upload_id), failure)
                _delete_quietly(object_storage, upload.object_key)
                raise HTTPException(status_code=422, detail={"code": failure})
            finalized = object_storage.finalize_upload(upload)
            failure = _verification_failure(upload, finalized)
            if failure is not None:
                _delete_quietly(object_storage, finalized.object_key)
                store.mark_failed(principal.user_id, str(upload_id), failure)
                _delete_quietly(object_storage, upload.object_key)
                raise HTTPException(status_code=422, detail={"code": failure})
            try:
                completed = store.mark_ready(principal.user_id, str(upload_id), finalized.object_key)
            except (UploadExpired, UploadNotFound):
                _delete_quietly(object_storage, finalized.object_key)
                raise
            if completed.object_key != finalized.object_key:
                _delete_quietly(object_storage, finalized.object_key)
            _delete_quietly(object_storage, upload.object_key)
            return _completed_view(UploadCompleteView, completed)
        except UploadNotFound:
            raise HTTPException(status_code=404, detail={"code": "upload_not_found"}) from None
        except UploadExpired:
            raise HTTPException(status_code=410, detail={"code": "upload_expired"}) from None
        except UploadUnavailable:
            raise HTTPException(status_code=503, detail={"code": "pdf_upload_unavailable"}) from None
        except HTTPException:
            raise
        except RuntimeError:
            raise HTTPException(status_code=503, detail={"code": "pdf_upload_unavailable"}) from None

    @router.get("/papers/{paper_id}/versions/{version_id}/file-access",
                response_model=FileAccessView)
    def get_file_access(paper_id: UUID, version_id: str, principal: Principal, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if principal.credential_type != "web_session":
            raise HTTPException(status_code=403, detail={"code": "web_session_required"})
        version_id = version_id.strip()
        if (not version_id or len(version_id) > 128 or "\x00" in version_id
                or "/" in version_id or "\\" in version_id or PureWindowsPath(version_id).drive):
            raise HTTPException(status_code=422, detail={"code": "invalid_version_id"})
        try:
            file = store.get_file_access(principal.user_id, str(paper_id), version_id)
            if file is None:
                return FileAccessView(status="not_uploaded", reason="not_uploaded")
            values = {
                "status": file.status, "file_name": file.file_name,
                "file_size": file.file_size, "content_type": file.content_type,
            }
            if file.status != "ready":
                return FileAccessView(**values, reason=file.failure_reason or f"upload_{file.status}")
            prepared = object_storage.prepare_download(file)
            return FileAccessView(
                **values, url=prepared.url, expires_at=prepared.expires_at.isoformat())
        except UploadTargetNotFound:
            raise HTTPException(status_code=404, detail={"code": "paper_version_not_found"}) from None
        except (UploadUnavailable, RuntimeError):
            raise HTTPException(status_code=503, detail={"code": "pdf_access_unavailable"}) from None

    return router


def _verification_failure(upload: PendingUpload, actual) -> str | None:
    if actual.file_size != upload.file_size:
        return "upload_size_mismatch"
    if actual.content_type.split(";", 1)[0].strip().lower() != upload.content_type:
        return "upload_content_type_mismatch"
    if not actual.pdf_header_valid:
        return "upload_content_type_mismatch"
    if actual.sha256_hex != upload.sha256_hex:
        return "upload_checksum_mismatch"
    return None


def _delete_quietly(object_storage, object_key: str) -> None:
    try:
        object_storage.delete_upload(object_key)
    except Exception:
        pass


def _completed_view(view_type, completed: CompletedUpload):
    return view_type(
        upload_id=UUID(completed.upload_id), paper_id=UUID(completed.paper_id),
        version_id=completed.server_version_id, file_name=completed.file_name,
        file_size=completed.file_size, content_type=completed.content_type,
        storage_status="ready", failure_reason=None,
    )
