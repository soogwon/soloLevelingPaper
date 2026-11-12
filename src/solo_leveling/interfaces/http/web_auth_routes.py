"""외부 웹 로그인, 현재 사용자와 로그아웃 HTTP 계약."""

from typing import Any

from .web_auth import (
    CSRF_COOKIE, LOGIN_COOKIE, SESSION_COOKIE, matching_tokens, WebAuthenticationRejected, WebAuthenticationUnavailable,
)


def create_web_auth_router(flow: Any, store: Any):
    from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Response, status
    from fastapi.responses import RedirectResponse

    router = APIRouter(prefix="/v1", tags=["web-authentication"])

    @router.get("/auth/login")
    def login(return_to: str = Query(...), provider: str = Query("oidc")):
        if provider not in ("oidc", "google"):
            raise HTTPException(status_code=422, detail={"code": "unsupported_authentication_provider"})
        try:
            location, state = flow.start(return_to)
            response = RedirectResponse(location, status_code=status.HTTP_302_FOUND,
                                        headers={"Cache-Control": "no-store"})
            response.set_cookie(LOGIN_COOKIE, state, max_age=600, httponly=True,
                                secure=True, samesite="lax", path="/")
            return response
        except WebAuthenticationRejected:
            raise HTTPException(status_code=422, detail={"code": "invalid_return_to"}) from None
        except WebAuthenticationUnavailable:
            raise HTTPException(status_code=503, detail={"code": "authentication_unavailable"}) from None

    @router.get("/auth/callback")
    def callback(code: str = Query(...), state: str = Query(...),
                 login_state: str | None = Cookie(None, alias=LOGIN_COOKIE)):
        if not matching_tokens(state, login_state):
            raise HTTPException(status_code=401, detail={"code": "authentication_failed"})
        try:
            session, return_to = flow.complete(code, state)
        except WebAuthenticationRejected:
            raise HTTPException(status_code=401, detail={"code": "authentication_failed"}) from None
        except WebAuthenticationUnavailable:
            raise HTTPException(status_code=503, detail={"code": "authentication_unavailable"}) from None
        response = RedirectResponse(return_to, status_code=status.HTTP_303_SEE_OTHER,
                                    headers={"Cache-Control": "no-store"})
        response.delete_cookie(LOGIN_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        max_age = max(0, int((session.expires_at - flow.now()).total_seconds()))
        response.set_cookie(SESSION_COOKIE, session.token, max_age=max_age, httponly=True,
                            secure=True, samesite="lax", path="/")
        response.set_cookie(CSRF_COOKIE, session.csrf_token, max_age=max_age, httponly=False,
                            secure=True, samesite="lax", path="/")
        return response

    @router.get("/me")
    def me(session_token: str | None = Cookie(None, alias=SESSION_COOKIE)):
        if not session_token:
            raise HTTPException(status_code=401, detail={"code": "authentication_required"})
        try:
            identity = store.authenticate(session_token)
        except WebAuthenticationRejected:
            raise HTTPException(status_code=401, detail={"code": "authentication_required"}) from None
        except WebAuthenticationUnavailable:
            raise HTTPException(status_code=503, detail={"code": "authentication_unavailable"}) from None
        except Exception as exc:
            from .auth import AccessTokenRejected
            if isinstance(exc, AccessTokenRejected):
                raise HTTPException(status_code=401, detail={"code": "authentication_required"}) from None
            raise
        return {"user_id": identity.user_id, "display_name": identity.display_name, "email": identity.email}

    @router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(
        response: Response,
        session_token: str | None = Cookie(None, alias=SESSION_COOKIE),
        csrf_cookie: str | None = Cookie(None, alias=CSRF_COOKIE),
        csrf_header: str | None = Header(None, alias="X-CSRF-Token"),
    ):
        if (not session_token or not csrf_cookie or not csrf_header
                or not matching_tokens(csrf_cookie, csrf_header)):
            raise HTTPException(status_code=403, detail={"code": "csrf_validation_failed"})
        try:
            revoked = store.revoke(session_token, csrf_header)
        except WebAuthenticationUnavailable:
            raise HTTPException(status_code=503, detail={"code": "authentication_unavailable"}) from None
        if not revoked:
            raise HTTPException(status_code=401, detail={"code": "authentication_required"})
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        response.delete_cookie(CSRF_COOKIE, path="/", secure=True, httponly=False, samesite="lax")

    return router
