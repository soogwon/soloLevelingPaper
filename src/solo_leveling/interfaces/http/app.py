"""중앙 HTTP API 애플리케이션 팩터리."""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from .settings import HttpSettings, load_http_settings

if TYPE_CHECKING:
    from fastapi import FastAPI
    from .auth import AccessTokenVerifier
    from .device_auth import DeviceRegistry
    from .devices import DeviceStore
    from .papers import PaperStore


def create_app(
    settings: HttpSettings | None = None,
    token_verifier: "AccessTokenVerifier | None" = None,
    device_registry: "DeviceRegistry | None" = None,
    device_store: "DeviceStore | None" = None,
    paper_store: "PaperStore | None" = None,
    web_auth_flow=None,
    web_session_store=None,
) -> "FastAPI":
    """명시적 설정 또는 환경 설정으로 독립적인 API 앱을 만든다."""

    from fastapi import Depends, FastAPI

    from .auth import RejectAllTokenVerifier, create_auth_dependency
    from .device_auth import DeviceAuthorizingTokenVerifier, RejectAllDeviceRegistry
    from .device_routes import create_device_router, create_device_token_router
    from .device_tokens import OpaqueDeviceTokenVerifier, RoutingAccessTokenVerifier
    from .devices import PostgresDeviceStore, UnavailableDeviceStore
    from .oidc import OidcAccessTokenVerifier
    from .paper_routes import create_paper_router, create_web_paper_router
    from .papers import PostgresPaperStore, UnavailablePaperStore
    from .settings import load_database_settings, load_oidc_settings, load_web_auth_settings
    from .web_auth import OidcWebAuthFlow, PostgresWebSessionStore, WebSessionVerifier
    from .web_auth_routes import create_web_auth_router

    selected = settings or load_http_settings()
    managed_store = None
    if device_store is None:
        database = load_database_settings()
        if database is not None:
            managed_store = PostgresDeviceStore.from_dsn(database.dsn)
            device_store = managed_store
    selected_store = device_store if device_store is not None else UnavailableDeviceStore()
    if paper_store is None and managed_store is not None:
        paper_store = PostgresPaperStore(managed_store.connection_pool)
    selected_paper_store = paper_store if paper_store is not None else UnavailablePaperStore()

    @asynccontextmanager
    async def lifespan(_app):
        if managed_store is not None:
            managed_store.open()
        try:
            yield
        finally:
            if managed_store is not None:
                managed_store.close()

    app = FastAPI(
        title="Solo Leveling Paper API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = selected
    if token_verifier is None:
        oidc_settings = load_oidc_settings()
        token_verifier = OidcAccessTokenVerifier(oidc_settings) if oidc_settings else RejectAllTokenVerifier()
    web_access_verifier = token_verifier
    web_settings = load_web_auth_settings()
    if web_settings is not None:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[web_settings.app_origin.rstrip("/")],
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"],
        )
    if web_session_store is None and managed_store is not None and web_settings is not None:
        web_session_store = PostgresWebSessionStore(managed_store.connection_pool)
    if web_auth_flow is None and web_session_store is not None and web_settings is not None:
        web_auth_flow = OidcWebAuthFlow.create(web_settings, web_session_store, web_access_verifier)
    token_verifier = RoutingAccessTokenVerifier(token_verifier, OpaqueDeviceTokenVerifier(selected_store))
    if device_registry is not None:
        selected_registry = device_registry
    elif device_store is not None:
        selected_registry = device_store
    else:
        selected_registry = RejectAllDeviceRegistry()
    token_verifier = DeviceAuthorizingTokenVerifier(token_verifier, selected_registry)
    session_verifier = WebSessionVerifier(web_session_store) if web_session_store is not None else None
    require_principal = create_auth_dependency(token_verifier, session_verifier)

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

    app.include_router(create_device_router(require_principal, selected_store))
    app.include_router(create_device_token_router(selected_store))
    app.include_router(create_paper_router(require_principal, selected_paper_store))
    app.include_router(create_web_paper_router(require_principal, selected_paper_store))
    if web_auth_flow is not None and web_session_store is not None:
        app.include_router(create_web_auth_router(web_auth_flow, web_session_store))

    return app
