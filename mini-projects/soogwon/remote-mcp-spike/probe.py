"""#388 전용 합성 연결 실험. 제품 서버와 인증 제공자를 변경하지 않는다."""

import asyncio
import math
import time
from typing import Any
from urllib.parse import urlsplit

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse


SCOPE = "spike:read"
CARD_URI = "ui://remote-spike/card.html"
TOOLS = frozenset({"whoami", "probe_card", "probe_delay"})


class DenyAll:
    async def verify_token(self, token):
        return None


class BoundaryVerifier:
    """외부 검증기가 서명·issuer를 검사한 뒤 실험의 최소 권한을 다시 확인한다."""

    def __init__(self, delegate, resource):
        self.delegate = delegate
        self.resource = resource

    async def verify_token(self, token):
        try:
            value = await self.delegate.verify_token(token)
            if (value is None or value.resource != self.resource
                    or not value.subject or not value.subject.strip()
                    or value.expires_at is None or value.expires_at <= time.time()):
                return None
            return value
        except Exception:
            # 제공자 오류의 본문·토큰을 응답이나 로그에 노출하지 않는다.
            return None


class NoSessionApp:
    """이 준비 단계는 상태 없는 전송만 검증하며 세션 ID를 수용하지 않는다."""

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


def _https_url(value):
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment):
        raise ValueError("자격정보·쿼리·fragment가 없는 HTTPS URL이 필요합니다.")
    return AnyHttpUrl(value)


def create_probe(*, resource_url: str, issuer_url: str, verifier: TokenVerifier | None = None):
    """기본은 전부 거부한다. 합성 검증기는 테스트에서만 주입한다."""
    resource = _https_url(resource_url)
    issuer = _https_url(issuer_url)
    if urlsplit(str(resource)).path != "/mcp":
        raise ValueError("실험 리소스 경로는 /mcp여야 합니다.")
    server = FastMCP(
        "remote-mcp-spike", stateless_http=True, json_response=True,
        token_verifier=BoundaryVerifier(verifier or DenyAll(), str(resource)),
        auth=AuthSettings(
            issuer_url=issuer, resource_server_url=resource,
            required_scopes=[SCOPE], validate_token_resource=True,
        ),
    )

    @server.tool()
    def whoami() -> dict[str, Any]:
        """인증 통과 여부만 반환하며 사용자 식별자와 토큰은 반환하지 않는다."""
        return {"authenticated": True, "environment": "synthetic-spike"}

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
        """최대 5초의 합성 지연만 허용한다. 실제 처리 시간 측정이 아니다."""
        if not math.isfinite(seconds) or not 0 <= seconds <= 5:
            raise ValueError("지연은 0~5초 범위여야 합니다.")
        await asyncio.sleep(seconds)
        return {"completed": True, "delay_seconds": seconds}

    return NoSessionApp(server.streamable_http_app())
