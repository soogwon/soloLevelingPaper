"""기존 동의·생성 원장을 재사용하는 원격 메모 작성 확인 카드."""
from dataclasses import asdict
from pathlib import Path
from typing import Any
from mcp.types import ToolAnnotations
from solo_leveling.interfaces.http.remote_annotation_recovery_routes import RecoveryInput


CARD_URI = 'ui://remote-annotations/compose.html'


def register_annotation_cards(server, service, call, context, binding):
    @server.resource(CARD_URI, mime_type='text/html;profile=mcp-app')
    def remote_annotation_compose_card():
        return Path(__file__).with_name('assets').joinpath('remote-annotation-compose.html').read_text(encoding='utf-8')

    @server.tool(meta={'ui': {'resourceUri': CARD_URI}},
                 annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    def prepare_remote_annotation(context_id: str, value: dict) -> dict[str, Any]:
        """메모·하이라이트 초기 입력 전체로 기존 미확정 요청을 먼저 찾고 작성 확인 카드를 연다. 준비만으로 학습 시작·동의·생성·AI 전송하지 않는다. 키가 있으면 기존 복구 카드를 사용한다."""
        def operation(owner):
            ctx = context(context_id)
            parsed = RecoveryInput.model_validate(value).domain()
            if service.learning.get(owner, ctx) is None:
                return None
            existing = service.recover(owner, ctx, parsed)
            return {'context_id': ctx, 'value': value, 'binding': binding(owner, ctx),
                    'existing': asdict(existing) if existing is not None else None}
        return call(False, operation)

    @server.tool(meta={'ui': {'visibility': ['app']}},
                 annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    def save_remote_annotation_card(context_id: str, request_key: str, value: dict,
                                    expected_binding: str) -> dict[str, Any]:
        """사용자가 새 기록 저장을 명시적으로 확인한 카드 입력을 같은 키로 저장한다. 서버 저장 동의를 별도로 검사한다. 응답 유실 시 새 키로 재접수하지 않는다."""
        import hmac
        def operation(owner):
            ctx = context(context_id)
            if not hmac.compare_digest(binding(owner, ctx), expected_binding):
                return {'connection_changed': True}
            result = service.create(owner, ctx, request_key, RecoveryInput.model_validate(value).domain())
            return asdict(result)
        return call(True, operation)
