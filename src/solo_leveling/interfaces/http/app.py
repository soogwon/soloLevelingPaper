"""중앙 HTTP API 애플리케이션 팩터리."""

from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING

from .settings import HttpSettings, load_http_settings

if TYPE_CHECKING:
    from fastapi import FastAPI
    from .auth import AccessTokenVerifier
    from .device_auth import DeviceRegistry
    from .devices import DeviceStore
    from .papers import PaperStore
    from .collections import CollectionStore
    from .resume_requests import ResumeRequestStore
    from .settings import RemoteMcpSettings
    from mcp.server.auth.provider import TokenVerifier


def create_app(
    settings: HttpSettings | None = None,
    token_verifier: "AccessTokenVerifier | None" = None,
    device_registry: "DeviceRegistry | None" = None,
    device_store: "DeviceStore | None" = None,
    paper_store: "PaperStore | None" = None,
    collection_store: "CollectionStore | None" = None,
    resume_store: "ResumeRequestStore | None" = None,
    file_upload_store=None,
    object_storage=None,
    remote_upload_store=None,
    remote_consent_store=None,
    remote_processing_intake=None,
    web_auth_flow=None,
    web_session_store=None,
    knowledge_graph=None,
    remote_mcp_settings: "RemoteMcpSettings | None" = None,
    remote_mcp_verifier: "TokenVerifier | None" = None,
    deletion_store=None,
    remote_product_queries=None,
    remote_questions=None,
    remote_annotation_recovery=None,
    remote_annotation_products=None,
    remote_mcp_annotations=None,
) -> "FastAPI":
    """명시적 설정 또는 환경 설정으로 독립적인 API 앱을 만든다."""

    from fastapi import Depends, FastAPI

    from .auth import RejectAllTokenVerifier, create_auth_dependency
    from .annotation_routes import create_annotation_router
    from .deletion_routes import create_deletion_router
    from .remote_deletions import PostgresRemoteDeletionStore
    from .collection_routes import create_collection_router
    from .collections import PostgresCollectionStore, UnavailableCollectionStore
    from .device_auth import DeviceAuthorizingTokenVerifier, RejectAllDeviceRegistry
    from .device_routes import create_device_router, create_device_token_router
    from .device_tokens import OpaqueDeviceTokenVerifier, RoutingAccessTokenVerifier
    from .devices import PostgresDeviceStore, UnavailableDeviceStore
    from .oidc import OidcAccessTokenVerifier
    from .paper_routes import create_paper_router, create_web_paper_router
    from .file_routes import create_file_router
    from .file_uploads import PostgresFileUploadStore, UnavailableFileUploadStore
    from .object_storage import S3ObjectStorage, UnavailableObjectStorage
    from .papers import PostgresPaperStore, UnavailablePaperStore
    from .resume_requests import PostgresResumeRequestStore, UnavailableResumeRequestStore
    from .resume_routes import create_resume_router
    from .remote_mcp import RemoteMcpDispatchMiddleware, create_remote_mcp
    from .remote_upload_routes import create_remote_upload_router
    from .remote_upload_consent_routes import create_remote_upload_consent_router
    from .remote_upload_consents import (
        PostgresRemoteUploadConsentStore,
        UnavailableRemoteUploadConsentStore,
    )
    from .remote_processing_intake import (
        PostgresRemoteProcessingIntake,
        UnavailableRemoteProcessingIntake,
    )
    from .remote_uploads import (
        PostgresRemoteUploadIntentStore,
        RemoteUploadVerificationService,
        UnavailableRemoteUploadIntentStore,
    )
    from .knowledge_graph_routes import create_knowledge_graph_router, load_server_graph
    from .settings import (
        load_database_settings,
        load_oidc_settings,
        load_object_storage_settings,
        load_remote_mcp_settings,
        load_web_auth_settings,
        load_web_static_root,
    )
    from .web_auth import OidcWebAuthFlow, PostgresWebSessionStore, WebSessionVerifier
    from .web_auth_routes import create_web_auth_router

    selected = settings or load_http_settings()
    managed_store = None
    managed_remote_store = None
    if device_store is None:
        database = load_database_settings()
        if database is not None:
            managed_store = PostgresDeviceStore.from_dsn(database.dsn)
            device_store = managed_store
    selected_store = device_store if device_store is not None else UnavailableDeviceStore()
    if deletion_store is None and managed_store is not None:
        deletion_store = PostgresRemoteDeletionStore(managed_store.connection_pool)
    if paper_store is None and managed_store is not None:
        paper_store = PostgresPaperStore(managed_store.connection_pool)
    selected_paper_store = paper_store if paper_store is not None else UnavailablePaperStore()
    if collection_store is None and managed_store is not None:
        collection_store = PostgresCollectionStore(managed_store.connection_pool)
    selected_collection_store = (collection_store if collection_store is not None
                                 else UnavailableCollectionStore())
    if resume_store is None and managed_store is not None:
        resume_store = PostgresResumeRequestStore(managed_store.connection_pool)
    selected_resume_store = resume_store if resume_store is not None else UnavailableResumeRequestStore()
    if file_upload_store is None and managed_store is not None:
        file_upload_store = PostgresFileUploadStore(managed_store.connection_pool)
    selected_file_upload_store = (file_upload_store if file_upload_store is not None
                                  else UnavailableFileUploadStore())
    if remote_upload_store is None and managed_store is not None:
        remote_upload_store = PostgresRemoteUploadIntentStore(managed_store.connection_pool)
    selected_remote_upload_store = (remote_upload_store if remote_upload_store is not None
                                    else UnavailableRemoteUploadIntentStore())
    if remote_consent_store is None and managed_store is not None:
        remote_consent_store = PostgresRemoteUploadConsentStore(managed_store.connection_pool)
    selected_remote_consent_store = (remote_consent_store if remote_consent_store is not None
                                     else UnavailableRemoteUploadConsentStore())
    if remote_processing_intake is None and managed_store is not None:
        remote_processing_intake = PostgresRemoteProcessingIntake(managed_store.connection_pool)
    selected_remote_processing_intake = (
        remote_processing_intake if remote_processing_intake is not None
        else UnavailableRemoteProcessingIntake())
    if object_storage is None:
        storage_settings = load_object_storage_settings()
        object_storage = (S3ObjectStorage.create(storage_settings) if storage_settings is not None
                          else UnavailableObjectStorage())
    selected_remote_mcp = (remote_mcp_settings if remote_mcp_settings is not None
                           else load_remote_mcp_settings())
    if selected_remote_mcp.enabled and selected_remote_mcp.product_reads_enabled and remote_product_queries is None:
        from os import environ
        from .settings import DatabaseSettings
        from solo_leveling.interfaces.mcp.remote_product import RemoteProductQueries
        dsn = environ.get('REMOTE_MCP_DATABASE_URL', '').strip()
        if dsn:
            database = DatabaseSettings(dsn)
            managed_remote_store = PostgresDeviceStore.from_dsn(database.dsn)
            remote_product_queries = RemoteProductQueries(managed_remote_store.connection_pool)
    managed_question_store = None
    if remote_questions is None:
        from .remote_question_settings import load_question_settings
        question_settings = load_question_settings()
        if question_settings is not None:
            from os import environ
            from .settings import DatabaseSettings
            from .remote_question_reviews import PostgresQuestionReviews
            dsn = environ.get('REMOTE_QUESTION_DATABASE_URL','').strip()
            if not selected_remote_mcp.enabled or not selected_remote_mcp.product_reads_enabled or not dsn:
                raise ValueError('question_settings_invalid')
            managed_question_store = PostgresDeviceStore.from_dsn(DatabaseSettings(dsn).dsn)
            remote_questions = PostgresQuestionReviews(managed_question_store.connection_pool,**question_settings)
    managed_annotation_stores = []
    from .remote_annotation_settings import load_annotation_settings
    annotation_settings = load_annotation_settings()
    if annotation_settings is not None:
        if (not selected_remote_mcp.enabled or not selected_remote_mcp.product_reads_enabled
                or remote_product_queries is None or remote_annotation_products is not None
                or remote_mcp_annotations is not None or remote_annotation_recovery is not None):
            raise ValueError('annotation_settings_invalid')
        from .remote_annotation_products import RemoteAnnotationProducts
        for dsn in (annotation_settings.web_dsn, annotation_settings.mcp_dsn):
            managed_annotation_stores.append(PostgresDeviceStore.from_dsn(dsn))
        remote_annotation_products, remote_mcp_annotations = (
            RemoteAnnotationProducts(store.connection_pool, annotation_settings.service_id)
            for store in managed_annotation_stores)
    remote_bundle = (create_remote_mcp(selected_remote_mcp, remote_mcp_verifier, product_queries=remote_product_queries, question_service=remote_questions, annotation_service=remote_mcp_annotations)
                     if selected_remote_mcp.enabled else None)

    @asynccontextmanager
    async def lifespan(_app):
        async with AsyncExitStack() as stack:
            if managed_store is not None:
                managed_store.open()
                stack.callback(managed_store.close)
            if managed_remote_store is not None:
                managed_remote_store.open()
                stack.callback(managed_remote_store.close)
            if managed_question_store is not None:
                managed_question_store.open()
                stack.callback(managed_question_store.close)
            for store in managed_annotation_stores:
                stack.callback(store.close)
                store.open()
            if remote_bundle is not None:
                await stack.enter_async_context(remote_bundle.server.session_manager.run())
            yield

    app = FastAPI(
        title="Solo Leveling Paper API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = selected
    if remote_bundle is not None:
        # SDK의 인증·DNS rebinding middleware를 잃지 않고 두 공개 경로만 먼저 분기한다.
        app.add_middleware(RemoteMcpDispatchMiddleware, remote_app=remote_bundle.app)
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
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
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
    app.include_router(create_web_paper_router(
        require_principal, selected_paper_store, selected_file_upload_store))
    app.include_router(create_file_router(require_principal, selected_file_upload_store, object_storage))
    app.include_router(create_remote_upload_router(
        require_principal, selected_remote_upload_store,
        RemoteUploadVerificationService(selected_remote_upload_store, object_storage)))
    app.include_router(create_remote_upload_consent_router(
        require_principal, selected_remote_consent_store, selected_remote_processing_intake))
    from .remote_question_routes import create_question_review_router
    app.include_router(create_question_review_router(require_principal,remote_questions))
    if remote_annotation_products is not None:
        if remote_annotation_recovery is None:
            remote_annotation_recovery = remote_annotation_products
        from .remote_annotation_product_routes import create_annotation_product_router
        app.include_router(create_annotation_product_router(require_principal, remote_annotation_products))
    if remote_annotation_recovery is not None:
        from .remote_annotation_recovery_routes import create_annotation_recovery_router
        app.include_router(create_annotation_recovery_router(require_principal, remote_annotation_recovery))
    app.include_router(create_annotation_router(require_principal, selected_paper_store))
    app.include_router(create_deletion_router(require_principal, deletion_store))
    app.include_router(create_collection_router(require_principal, selected_collection_store))
    app.include_router(create_resume_router(require_principal, selected_resume_store))
    selected_graph = knowledge_graph if knowledge_graph is not None else load_server_graph()
    app.include_router(create_knowledge_graph_router(require_principal, selected_graph))
    if web_auth_flow is not None and web_session_store is not None:
        app.include_router(create_web_auth_router(web_auth_flow, web_session_store))
    web_static_root = load_web_static_root()
    if web_static_root is not None:
        # 루트 mount는 반드시 API route 등록 뒤에 둬야 /v1과 /health를 가리지 않는다.
        app.mount("/", _revalidating_static_files(web_static_root), name="external-site")

    return app


def _revalidating_static_files(directory):
    """웹 파일은 매번 ETag로 재확인한다. 배포 직후에도 브라우저가 이전 JS·CSS를 쓰지 않는다.

    파일 이름에 빌드 해시를 붙이지 않으므로 장기 캐시 대신 no-cache를 쓴다. 바뀌지 않은 파일은
    304 응답으로 본문 없이 재사용된다.
    """
    from fastapi.staticfiles import StaticFiles

    class RevalidatingStaticFiles(StaticFiles):
        def file_response(self, *args, **kwargs):
            response = super().file_response(*args, **kwargs)
            response.headers["Cache-Control"] = "no-cache"
            return response

    return RevalidatingStaticFiles(directory=directory, html=True)
