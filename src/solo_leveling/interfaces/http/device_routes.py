"""웹 세션 사용자의 기기 등록·목록·해제 API."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from .auth import AuthenticatedPrincipal
from .device_tokens import DeviceCredentialRejected, DeviceTokenPair
from .devices import DeviceRecord, DeviceStore, DeviceStoreUnavailable


def create_device_router(require_principal: Any, store: DeviceStore):
    from fastapi import APIRouter, Depends, HTTPException, Response, status
    from pydantic import BaseModel, Field, field_validator

    class DeviceCreate(BaseModel):
        display_name: str = Field(min_length=1, max_length=100)

        @field_validator("display_name")
        @classmethod
        def normalize_display_name(cls, value: str) -> str:
            normalized = value.strip()
            if not normalized:
                raise ValueError("display_name은 공백일 수 없습니다.")
            if "\x00" in normalized:
                raise ValueError("display_name에 NUL 문자를 사용할 수 없습니다.")
            return normalized

    class DeviceView(BaseModel):
        device_id: str
        display_name: str
        created_at: datetime

    class TokenView(BaseModel):
        token_type: str = "Bearer"
        access_token: str
        access_expires_at: datetime
        refresh_token: str
        refresh_expires_at: datetime
        device_id: str

    router = APIRouter(prefix="/v1/devices", tags=["devices"])
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    def web_user(principal: AuthenticatedPrincipal) -> str:
        if principal.credential_type != "web_session":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "web_session_required"})
        return principal.user_id

    def view(record: DeviceRecord) -> DeviceView:
        return DeviceView(device_id=record.device_id, display_name=record.display_name, created_at=record.created_at)

    def token_view(pair: DeviceTokenPair) -> TokenView:
        return TokenView(**pair.__dict__)

    @router.post("", response_model=DeviceView, status_code=status.HTTP_201_CREATED)
    def register(payload: DeviceCreate, principal: Principal):
        try:
            return view(store.register(web_user(principal), payload.display_name))
        except DeviceStoreUnavailable:
            raise _unavailable(HTTPException, status.HTTP_503_SERVICE_UNAVAILABLE) from None

    @router.get("", response_model=list[DeviceView])
    def list_devices(principal: Principal):
        try:
            return [view(item) for item in store.list_active(web_user(principal))]
        except DeviceStoreUnavailable:
            raise _unavailable(HTTPException, status.HTTP_503_SERVICE_UNAVAILABLE) from None

    @router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
    def revoke(device_id: UUID, principal: Principal):
        try:
            store.revoke(web_user(principal), str(device_id))
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        except DeviceStoreUnavailable:
            raise _unavailable(HTTPException, status.HTTP_503_SERVICE_UNAVAILABLE) from None

    @router.post("/{device_id}/credentials", response_model=TokenView)
    def issue_credentials(device_id: UUID, principal: Principal, response: Response):
        try:
            pair = store.issue_credentials(web_user(principal), str(device_id))
        except DeviceCredentialRejected:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "device_not_found"}) from None
        except DeviceStoreUnavailable:
            raise _unavailable(HTTPException, status.HTTP_503_SERVICE_UNAVAILABLE) from None
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return token_view(pair)

    return router


def create_device_token_router(store: DeviceStore):
    from fastapi import APIRouter, HTTPException, Response, status
    from pydantic import BaseModel, Field

    class RefreshRequest(BaseModel):
        refresh_token: str = Field(min_length=40, max_length=200)

    class TokenView(BaseModel):
        token_type: str = "Bearer"
        access_token: str
        access_expires_at: datetime
        refresh_token: str
        refresh_expires_at: datetime
        device_id: str

    router = APIRouter(prefix="/v1/device-token", tags=["devices"])

    @router.post("/refresh", response_model=TokenView)
    def refresh(payload: RefreshRequest, response: Response):
        try:
            pair = store.rotate_credentials(payload.refresh_token)
        except DeviceCredentialRejected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "invalid_device_credential"},
            ) from None
        except DeviceStoreUnavailable:
            raise _unavailable(HTTPException, status.HTTP_503_SERVICE_UNAVAILABLE) from None
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return TokenView(**pair.__dict__)

    return router


def _unavailable(exception_type, status_code: int):
    return exception_type(status_code=status_code, detail={"code": "device_store_unavailable"})
