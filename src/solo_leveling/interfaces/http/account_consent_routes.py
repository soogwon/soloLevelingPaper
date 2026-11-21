"""인증된 웹 세션에서만 계정 단위 동의를 조회·기록·철회한다."""
from datetime import datetime
from typing import Annotated, Any, Literal

from .auth import AuthenticatedPrincipal


def create_account_consent_router(require_principal: Any, consents: Any):
    from fastapi import APIRouter, Depends, HTTPException, Response
    from pydantic import BaseModel, ConfigDict, Field

    Purpose = Literal['storage', 'ai_provider']

    class ItemView(BaseModel):
        purpose: Purpose
        required: bool
        notice: str
        state: Literal['missing', 'current', 'stale', 'revoked']
        revision: int
        granted_at: datetime | None
        revoked_at: datetime | None

    class StatusView(BaseModel):
        notice_version: str
        complete: bool
        items: list[ItemView]

    class GrantRequest(BaseModel):
        model_config = ConfigDict(extra='forbid')
        notice_version: str = Field(min_length=1, max_length=100)
        purposes: list[Purpose] = Field(min_length=1, max_length=2)

    router = APIRouter(prefix='/v1/account/consents', tags=['account-consent'])
    Principal = Annotated[AuthenticatedPrincipal, Depends(require_principal)]

    def require_web(principal):
        if principal.credential_type != 'web_session':
            raise HTTPException(status_code=403, detail={'code': 'web_session_required'})

    def run(action):
        try:
            return action()
        except ValueError as exc:
            code = str(exc)
            status = 409 if code == 'account_consent_notice_changed' else 422
            raise HTTPException(status_code=status, detail={'code': code}) from None
        except Exception:
            raise HTTPException(status_code=503, detail={'code': 'account_consent_unavailable'}) from None

    @router.get('', response_model=StatusView)
    def get_consents(principal: Principal, response: Response):
        require_web(principal)
        response.headers['Cache-Control'] = 'no-store'
        return run(lambda: consents.view(principal.user_id))

    @router.post('', response_model=StatusView)
    def grant_consents(payload: GrantRequest, principal: Principal, response: Response):
        require_web(principal)
        response.headers['Cache-Control'] = 'no-store'
        return run(lambda: consents.grant(principal.user_id, payload.purposes, payload.notice_version))

    @router.post('/{purpose}/revoke', response_model=StatusView)
    def revoke_consent(purpose: Purpose, principal: Principal, response: Response):
        require_web(principal)
        response.headers['Cache-Control'] = 'no-store'
        return run(lambda: consents.revoke(principal.user_id, purpose))

    return router
