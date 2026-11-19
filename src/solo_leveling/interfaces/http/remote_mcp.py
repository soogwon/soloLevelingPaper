"""#388의 공개 Auth0 연결만 실측하는 최소 원격 MCP 경계."""

import asyncio
import math
from dataclasses import dataclass
from typing import Any

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse

from .remote_mcp_auth import Auth0McpTokenVerifier
from .settings import RemoteMcpSettings


CARD_URI = "ui://remote-spike/card.html"
TOOLS = frozenset({"whoami", "probe_card", "probe_delay"})


@dataclass(frozen=True)
class RemoteMcpBundle:
    server: FastMCP
    app: Any


class NoSessionMiddleware:
    """#388 초기 스파이크는 상태 없는 전송만 허용한다."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            if b"mcp-session-id" in headers:
                response = JSONResponse({"error": "spike_sessions_not_supported"}, status_code=400)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class RemoteMcpDispatchMiddleware:
    """MCP 외부 경로만 SDK ASGI 앱으로 보내 인증·전송 middleware를 보존한다."""

    PATHS = frozenset({"/mcp", "/.well-known/oauth-protected-resource/mcp"})

    def __init__(self, app, remote_app):
        self.app = app
        self.remote_app = remote_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") in self.PATHS:
            await self.remote_app(scope, receive, send)
            return
        await self.app(scope, receive, send)


class PublicBoundaryMiddleware:
    """인증 전에 헤더 경계를 검사하고 원본 Host·Origin을 로그에 남기지 않는다."""

    def __init__(self, app, settings):
        self.app = app
        self.hosts = {value.encode() for value in settings.allowed_hosts}
        self.origins = {value.encode() for value in settings.allowed_origins}

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            headers = scope.get('headers', [])
            def values(name):
                return [v for k, v in headers if k.lower() == name]
            hosts = values(b'host')
            invalid = len(hosts) != 1 or hosts[0] not in self.hosts
            forwarded = values(b'x-forwarded-host')
            invalid |= bool(forwarded) and forwarded != hosts
            proto = values(b'x-forwarded-proto')
            invalid |= bool(proto) and proto != [b'https']
            invalid |= bool(values(b'forwarded'))
            origins = values(b'origin')
            bad_origin = bool(origins) and (len(origins) != 1 or origins[0] not in self.origins)
            if invalid or bad_origin:
                await JSONResponse({'error': 'invalid_public_boundary'},
                    status_code=421 if invalid else 403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_remote_mcp(
    settings: RemoteMcpSettings,
    verifier: TokenVerifier | None = None,
    *, product_queries=None, question_service=None, annotation_service=None, cancellation_service=None, summary_service=None, reflection_service=None, quiz_service=None,
) -> RemoteMcpBundle:
    if not settings.enabled:
        raise ValueError("원격 MCP 설정이 활성화되어야 합니다.")
    server = FastMCP(
        "solo-leveling-remote-spike",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/mcp",
        token_verifier=verifier if verifier is not None else Auth0McpTokenVerifier(settings),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.issuer),
            resource_server_url=AnyHttpUrl(settings.resource_url),
            required_scopes=list(dict.fromkeys([settings.required_scope] +
                (["papers:read"] if settings.product_reads_enabled else []) +
                (["questions:write"] if question_service is not None else []) +
                (["annotations:read", "annotations:write"] if annotation_service is not None else []))),
            validate_token_resource=True,
        ),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.allowed_hosts),
            allowed_origins=list(settings.allowed_origins),
        ),
    )

    @server.tool()
    def whoami() -> dict[str, Any]:
        """인증 성공 여부만 반환하며 사용자·client·token은 노출하지 않는다."""
        return {"authenticated": True, "environment": "staging-spike"}

    @server.tool(meta={"ui": {"resourceUri": CARD_URI}})
    def probe_card() -> dict[str, Any]:
        """카드 미지원 호스트에서도 읽을 수 있는 합성 안내를 반환한다."""
        return {"message": "원격 연결 합성 카드입니다. 저장·처리·외부 전송은 하지 않습니다."}

    @server.resource(CARD_URI, mime_type="text/html;profile=mcp-app")
    def card() -> str:
        return ('<!doctype html><html lang="ko"><meta charset="utf-8">'
                '<title>연결 확인</title><body><h1>원격 연결 합성 카드</h1>'
                '<p>저장·처리·외부 전송은 하지 않습니다.</p></body></html>')

    @server.tool()
    async def probe_delay(seconds: float = 0) -> dict[str, Any]:
        """실제 처리 없이 최대 5초의 호스트·프록시 경계만 확인한다."""
        if not math.isfinite(seconds) or not 0 <= seconds <= 5:
            raise ValueError("지연은 0~5초 범위여야 합니다.")
        await asyncio.sleep(seconds)
        return {"completed": True, "delay_seconds": seconds}

    if settings.product_reads_enabled:
        if product_queries is None:
            raise ValueError("remote_product_queries_required")
        from solo_leveling.interfaces.mcp.remote_product import register_product_queries
        register_product_queries(server, product_queries, cancellation_enabled=cancellation_service is not None)
        from solo_leveling.interfaces.mcp.remote_learning_path import register_remote_learning_path
        register_remote_learning_path(server, product_queries)
        if cancellation_service is not None:
            from solo_leveling.interfaces.mcp.remote_job_cancellation import register_job_cancellation_tools
            register_job_cancellation_tools(server, product_queries, cancellation_service)
        if question_service is not None:
            from solo_leveling.interfaces.mcp.remote_questions import register_question_tools
            register_question_tools(server,product_queries,question_service)

        if summary_service is not None:
            from solo_leveling.interfaces.mcp.remote_summaries import register_summary_tools
            register_summary_tools(server, product_queries, summary_service)

        if reflection_service is not None:
            from solo_leveling.interfaces.mcp.remote_reflection import register_remote_reflection
            register_remote_reflection(server,product_queries,reflection_service)
        if quiz_service is not None:
            from solo_leveling.interfaces.mcp.remote_quiz import register_remote_quiz
            register_remote_quiz(server,product_queries,quiz_service)

        if annotation_service is not None:
            from solo_leveling.interfaces.mcp.remote_annotations import register_annotation_tools
            register_annotation_tools(server, product_queries, annotation_service)

    if (cancellation_service is not None or summary_service is not None or reflection_service is not None or quiz_service is not None) and not settings.product_reads_enabled:
        raise ValueError('processing_cancel_settings_invalid')
    app = server.streamable_http_app()
    if cancellation_service is not None or summary_service is not None or reflection_service is not None or quiz_service is not None:
        # 지원 권한만 확장한다. 읽기 전용 토큰의 기존 MCP 접근은 유지한다.
        from mcp.server.auth.routes import create_protected_resource_routes
        metadata = create_protected_resource_routes(
            resource_url=server.settings.auth.resource_server_url,
            authorization_servers=[server.settings.auth.issuer_url],
            scopes_supported=[*server.settings.auth.required_scopes,
                              *(['jobs:cancel'] if cancellation_service is not None else []),
                              *(['summaries:write'] if summary_service is not None else []),
                              *(['reflections:read','reflections:write'] if reflection_service is not None else []),
                              *(['quizzes:read','quizzes:write'] if quiz_service is not None else [])])
        paths = {route.path for route in metadata}
        app.router.routes[:] = [route for route in app.router.routes if getattr(route,'path',None) not in paths] + metadata
    app.add_middleware(NoSessionMiddleware)
    app.add_middleware(PublicBoundaryMiddleware, settings=settings)
    return RemoteMcpBundle(server, app)
