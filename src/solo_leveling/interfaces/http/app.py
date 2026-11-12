"""중앙 HTTP API 애플리케이션 팩터리."""

from typing import TYPE_CHECKING

from .settings import HttpSettings, load_http_settings

if TYPE_CHECKING:
    from fastapi import FastAPI
    from .auth import AccessTokenVerifier


def create_app(
    settings: HttpSettings | None = None,
    token_verifier: "AccessTokenVerifier | None" = None,
) -> "FastAPI":
    """명시적 설정 또는 환경 설정으로 독립적인 API 앱을 만든다."""

    from fastapi import Depends, FastAPI

    from .auth import RejectAllTokenVerifier, create_auth_dependency

    selected = settings or load_http_settings()

    app = FastAPI(
        title="Solo Leveling Paper API",
        version="0.1.0",
    )
    app.state.settings = selected
    if token_verifier is None:
        token_verifier = RejectAllTokenVerifier()
    require_principal = create_auth_dependency(token_verifier)

    @app.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "solo-leveling-api",
            "environment": selected.environment,
        }

    @app.get("/v1/session", tags=["authentication"])
    def session(principal=Depends(require_principal)) -> dict[str, str | bool | None]:
        """현재 자격이 검증됐는지 민감한 사용자 식별자 없이 확인한다."""

        return {
            "authenticated": True,
            "credential_type": principal.credential_type,
            "device_id": principal.device_id,
        }

    return app
