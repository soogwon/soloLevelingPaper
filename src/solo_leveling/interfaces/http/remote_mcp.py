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


def create_remote_mcp(
    settings: RemoteMcpSettings,
    verifier: TokenVerifier | None = None,
) -> RemoteMcpBundle:
    if not settings.enabled:
        raise ValueError("원격 MCP 설정이 활성화되어야 합니다.")
    server = FastMCP(
        "solo-leveling-remote-spike",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/mcp",
        token_verifier=verifier or Auth0McpTokenVerifier(settings),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.issuer),
            resource_server_url=AnyHttpUrl(settings.resource_url),
            required_scopes=[settings.required_scope],
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

    app = server.streamable_http_app()
    app.add_middleware(NoSessionMiddleware)
    return RemoteMcpBundle(server, app)
