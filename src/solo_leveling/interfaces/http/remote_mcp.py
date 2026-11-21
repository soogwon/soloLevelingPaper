"""#388의 공개 Auth0 연결만 실측하는 최소 원격 MCP 경계."""

import asyncio
import math
from dataclasses import dataclass
from importlib.resources import files
from typing import Any
from urllib.parse import urlsplit

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse

from solo_leveling.interfaces.mcp.web_links import attach_web_urls

from .remote_mcp_auth import Auth0McpTokenVerifier
from .settings import RemoteMcpSettings


CARD_URI = "ui://remote-spike/card.html"
PDF_REGISTRATION_URI = "ui://remote-paper/register-pdf.html"
WEB_LINK_INSTRUCTIONS = (
    "웹 화면이나 학습 페이지 주소를 안내할 때는 응답의 web_url·review_web_url·upload_url 같은 "
    "전체 https 주소를 그대로 링크로 보여준다. /#/로 시작하는 경로만 따로 주거나 "
    "도메인을 붙여 달라고 하지 않는다. 지금 학습 맥락의 주소는 open_remote_paper나 "
    "get_remote_learning_dashboard의 web_url을 쓰고, 응답에 없는 주소는 짐작하지 않는다."
)
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
    *, product_queries=None, question_service=None, annotation_service=None, cancellation_service=None, summary_service=None, reflection_service=None, quiz_service=None, quiz_review_service=None,
    reprocessing_job_reads=False,
) -> RemoteMcpBundle:
    if not settings.enabled:
        raise ValueError("원격 MCP 설정이 활성화되어야 합니다.")
    server = FastMCP(
        "solo-leveling-remote-spike",
        instructions=(
            "‘요약해줘’, ‘요약 생성’, ‘이 논문 요약’은 기본적으로 Claude 채팅에서 답한다. "
            "현재 학습 맥락의 고정 판본을 read_remote_paper_page로 읽고, "
            "연구 질문·핵심 접근·주요 결과를 각 한 문장과 확인한 PDF 쪽으로 보여준다. "
            "근거가 확인되면 별도 저장 요청을 기다리지 않고 prepare_remote_chat_summary를 호출한다. "
            "결과가 saved이면 가입 때 받은 저장 동의로 이미 저장된 것이므로 같은 답변에 ‘저장됨’과 "
            "인용·저장 취소 링크를 붙인다. review_required이면 ‘인용 확인하고 저장’ 링크를 붙인다. "
            "준비가 거절되거나 실패하면 채팅 요약을 그대로 보여주고 저장되지 않았다고 알린다. "
            "prepare_remote_summary는 사용자가 서버 생성을 명시적으로 요청했을 때만 사용한다. "
            "가입 때 두 동의를 받은 계정이면 준비와 동시에 접수되며 비용은 운영자가 부담한다. "
            "‘동의했어’나 ‘요약 확인해줘’는 새 접수 요청이 아니다. "
            "기존 요청의 상태나 저장된 결과를 조회하고, 예산 거절이면 자동 재제출하지 않는다. "
            if summary_service is not None else ""
        ) + WEB_LINK_INSTRUCTIONS if settings.product_reads_enabled else None,
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
        web_origin = f"https://{urlsplit(settings.resource_url).netloc}"
        upload_url = f"{web_origin}/#/uploads"

        @server.tool(meta={"ui": {"resourceUri": PDF_REGISTRATION_URI}},
                     annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                                 idempotentHint=True, openWorldHint=False))
        def open_remote_pdf_registration() -> dict[str, str]:
            """웹 PDF 등록 화면을 안내합니다. 파일 선택은 웹에서 하며 가입 때 받은 동의로 보관·처리합니다."""
            return {
                "upload_url": upload_url,
                "message": f"웹에서 PDF를 선택해 업로드하세요. 가입 동의를 아직 하지 않았다면 동의 화면이 먼저 나옵니다. {upload_url}",
            }

        @server.resource(PDF_REGISTRATION_URI, mime_type="text/html;profile=mcp-app",
                         meta={"ui": {"csp": {"connectDomains": [], "resourceDomains": []}}})
        def pdf_registration_card() -> str:
            return files("solo_leveling.interfaces.mcp").joinpath(
                "assets/remote-pdf-registration.html").read_text(encoding="utf-8")

        from solo_leveling.interfaces.mcp.remote_product import register_product_queries
        register_product_queries(server, product_queries, cancellation_enabled=cancellation_service is not None,
                                 reprocessing_enabled=reprocessing_job_reads)
        from solo_leveling.interfaces.mcp.remote_learning_path import register_remote_learning_path
        register_remote_learning_path(server, product_queries)
        if cancellation_service is not None:
            from solo_leveling.interfaces.mcp.remote_job_cancellation import register_job_cancellation_tools
            register_job_cancellation_tools(server, product_queries, cancellation_service)
        if question_service is not None:
            from solo_leveling.interfaces.mcp.remote_questions import register_question_tools
            register_question_tools(server,product_queries,question_service)

        latest_summary = None
        if summary_service is not None:
            from solo_leveling.interfaces.mcp.remote_summaries import register_summary_tools
            latest_summary = register_summary_tools(server, product_queries, summary_service)

        if reflection_service is not None:
            from solo_leveling.interfaces.mcp.remote_reflection import register_remote_reflection
            register_remote_reflection(server, product_queries, reflection_service, web_origin=web_origin)
        if quiz_review_service is not None:
            from solo_leveling.interfaces.mcp.remote_quiz_reviews import register_quiz_review_tools
            register_quiz_review_tools(server, product_queries, quiz_review_service)
        if quiz_service is not None:
            from solo_leveling.interfaces.mcp.remote_quiz import register_remote_quiz
            register_remote_quiz(server,product_queries,quiz_service)

        if annotation_service is not None:
            from solo_leveling.interfaces.mcp.remote_annotations import register_annotation_tools
            register_annotation_tools(server, product_queries, annotation_service, latest_summary=latest_summary)

        # 모든 도구를 등록한 뒤 감싸야 web_path 옆에 전체 web_url이 붙는다.
        attach_web_urls(server, web_origin)

    if (cancellation_service is not None or summary_service is not None or reflection_service is not None or quiz_service is not None or quiz_review_service is not None) and not settings.product_reads_enabled:
        raise ValueError('processing_cancel_settings_invalid')
    app = server.streamable_http_app()
    if cancellation_service is not None or summary_service is not None or reflection_service is not None or quiz_service is not None or quiz_review_service is not None:
        # 지원 권한만 확장한다. 읽기 전용 토큰의 기존 MCP 접근은 유지한다.
        from mcp.server.auth.routes import create_protected_resource_routes
        metadata = create_protected_resource_routes(
            resource_url=server.settings.auth.resource_server_url,
            authorization_servers=[server.settings.auth.issuer_url],
            scopes_supported=[*server.settings.auth.required_scopes,
                              *(['jobs:cancel'] if cancellation_service is not None else []),
                              *(['summaries:write'] if summary_service is not None else []),
                              *(['reflections:read','reflections:write'] if reflection_service is not None else []),
                              *(['quizzes:read','quizzes:write'] if quiz_service is not None else (['quizzes:write'] if quiz_review_service is not None else []))])
        paths = {route.path for route in metadata}
        app.router.routes[:] = [route for route in app.router.routes if getattr(route,'path',None) not in paths] + metadata
    app.add_middleware(NoSessionMiddleware)
    app.add_middleware(PublicBoundaryMiddleware, settings=settings)
    return RemoteMcpBundle(server, app)
