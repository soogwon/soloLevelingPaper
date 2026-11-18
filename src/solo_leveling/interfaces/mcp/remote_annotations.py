"""인증된 원격 메모 도구. 동의 부여 도구와 로컬 경로 인자는 노출하지 않는다."""
from dataclasses import asdict, is_dataclass
import time
import hashlib
import hmac
from pathlib import Path
from uuid import UUID
from typing import Any

from mcp.types import ToolAnnotations

from mcp.server.auth.middleware.auth_context import get_access_token
from solo_leveling.interfaces.http.remote_mcp_auth import RemoteIdentityAccessToken
from solo_leveling.interfaces.http.remote_annotation_recovery_routes import RecoveryInput
from solo_leveling.interfaces.http.remote_annotation_consents import AnnotationStorageConsentRequired
from solo_leveling.interfaces.http.remote_annotations import (
    RemoteAnnotationConflict, RemoteAnnotationDeleted, RemoteAnnotationReferenceError,
    RemoteAnnotationRecoveryAmbiguous,
)


def register_annotation_tools(server, queries, service):
    def call(write, operation):
        try:
            token = get_access_token()
            scope = 'annotations:write' if write else 'annotations:read'
            if (not isinstance(token, RemoteIdentityAccessToken) or token.verified_identity() is None
                    or type(token.expires_at) is not int or token.expires_at <= time.time()
                    or scope not in token.scopes):
                return {'status': 'not_authorized'}
            owner = queries.owner()
            if owner is None:
                return {'status': 'not_authorized'}
            result = operation(owner)
            if result is None:
                return {'status': 'not_found'}
            if is_dataclass(result):
                result = asdict(result)
            response = {'status': 'ok', 'result': result}
            if isinstance(result, dict) and result.get('learning_record_id') and result.get('context_id'):
                response['web_path'] = '/#/remote-learning/' + str(UUID(result['context_id']))
            return response
        except AnnotationStorageConsentRequired:
            return {'status': 'consent_required', 'message': '인증된 웹에서 이 학습 맥락의 메모 저장 동의를 확인하세요.'}
        except RemoteAnnotationRecoveryAmbiguous:
            return {'status': 'ambiguous', 'message': '복구 후보를 자동 선택하거나 새 키로 재생성하지 마세요.'}
        except RemoteAnnotationConflict:
            return {'status': 'conflict', 'message': '현재 기록과 revision을 다시 조회하세요.'}
        except RemoteAnnotationDeleted:
            return {'status': 'deleted', 'message': '삭제된 기록을 새 키로 재생성하지 마세요.'}
        except RemoteAnnotationReferenceError:
            return {'status': 'not_found'}
        except (ValueError, TypeError, AttributeError):
            return {'status': 'invalid_argument'}
        except Exception:
            return {'status': 'unavailable', 'message': '기존 요청 키로 조회하세요. 자동 재제출하지 마세요.'}

    def binding(owner, context_id):
        # 인증 토큰은 반환하지 않으며 갱신·계정 변경 시 과거 카드의 확인을 차단한다.
        token = get_access_token()
        payload = f"annotation-card-v1:{service.service_id}:{owner}:{context_id}"
        return hmac.new(token.token.encode(), payload.encode(), hashlib.sha256).hexdigest()

    card_uri = "ui://remote-annotations/recovery.html"

    @server.resource(card_uri, mime_type="text/html;profile=mcp-app")
    def remote_annotation_recovery_card():
        return Path(__file__).with_name('assets').joinpath('remote-annotation-recovery.html').read_text(encoding='utf-8')

    @server.tool(meta={"ui": {"resourceUri": card_uri}},
                 annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    def show_remote_annotation_recovery(context_id: str, request_key: str,
                                        expected_binding: str | None = None) -> dict[str, Any]:
        """기존 요청 키의 현재 결과를 카드와 텍스트로 조회한다. 신규 생성하지 않는다. 토큰 갱신 후에는 카드를 새로 열어야 한다."""
        def operation(owner):
            ctx = context(context_id)
            current = binding(owner, ctx)
            if expected_binding is not None and not hmac.compare_digest(current, expected_binding):
                return {'connection_changed': True}
            result = service.lookup(owner, ctx, request_key)
            if result is None:
                return None
            return {'context_id': ctx, 'request_key': request_key, 'binding': current,
                    'recovery': asdict(result)}
        return call(False, operation)

    @server.tool(meta={"ui": {"visibility": ["app"]}},
                 annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    def confirm_remote_annotation_card(context_id: str, request_key: str, annotation_id: str,
                                       revision: int, expected_binding: str) -> dict[str, Any]:
        """사용자가 복구 카드의 현재 결과를 확인했을 때만 수신 확정한다. 새 기록이나 AI 호출은 만들지 않는다."""
        def operation(owner):
            ctx = context(context_id)
            if not hmac.compare_digest(binding(owner, ctx), expected_binding):
                return {'connection_changed': True}
            return {'confirmed': service.confirm(owner, ctx, request_key, context(annotation_id), revision)}
        return call(True, operation)

    def context(value):
        return str(UUID(value))

    @server.tool()
    def start_remote_learning(context_id: str) -> dict:
        """사용자가 학습 시작을 명시적으로 요청할 때만 호출한다. 기존 맥락의 기록을 재사용하며 동의나 AI 실행을 시작하지 않는다."""
        return call(True, lambda owner: service.learning.start(owner, context(context_id)))

    @server.tool()
    def get_remote_learning(context_id: str) -> dict:
        """기존 학습 기록만 조회한다. 없는 기록을 만들지 않는다."""
        return call(False, lambda owner: service.learning.get(owner, context(context_id)))

    @server.tool()
    def create_remote_annotation(context_id: str, request_key: str, value: dict) -> dict:
        """명시적 학습 시작과 인증 웹의 맥락 저장 동의 후 메모·하이라이트를 저장한다. 같은 키를 보관하고 응답 유실 시 재생성하지 않는다. value는 kind/body/segments/step/creation_origin이다."""
        return call(True, lambda owner: service.create(owner, context(context_id), request_key, RecoveryInput.model_validate(value).domain()))

    @server.tool()
    def get_remote_annotation(context_id: str, annotation_id: str) -> dict:
        """현재 맥락의 살아 있는 기록과 revision을 조회한다."""
        return call(False, lambda owner: service.store.get_annotation(owner, context(context_id), context(annotation_id)))

    @server.tool()
    def list_remote_annotations(context_id: str, after: str | None = None, limit: int = 20) -> dict:
        """현재 학습 맥락의 메모·하이라이트를 커서로 조회한다. 새로고침은 첫 페이지부터 한다."""
        return call(False, lambda owner: service.store.list_annotations(owner, context(context_id), after=after, limit=limit))

    @server.tool()
    def update_remote_annotation(context_id: str, annotation_id: str, revision: int, body: str, step: str | None = None) -> dict:
        """현재 revision과 유효한 저장 동의로 본문·단계만 수정한다. 근거 구간은 바꾸지 않는다."""
        return call(True, lambda owner: service.update(owner, context(context_id), context(annotation_id), body, step, revision))

    @server.tool()
    def delete_remote_annotation(context_id: str, annotation_id: str, revision: int) -> dict:
        """명시적 삭제 요청으로 현재 revision을 삭제한다. 동의 철회 후에도 삭제할 수 있다."""
        return call(True, lambda owner: service.store.delete_annotation(owner, context(context_id), context(annotation_id), expected_revision=revision))

    @server.tool()
    def lookup_remote_annotation_request(context_id: str, request_key: str) -> dict:
        """응답이 유실된 기존 키의 현재 결과나 삭제 상태만 조회한다."""
        return call(False, lambda owner: service.lookup(owner, context(context_id), request_key))

    @server.tool()
    def recover_remote_annotation_request(context_id: str, value: dict) -> dict:
        """초기 입력 전체로 미확정 결과를 복구한다. 본문 일부 검색이나 새 키 생성으로 대체하지 않는다."""
        return call(False, lambda owner: service.recover(owner, context(context_id), RecoveryInput.model_validate(value).domain()))

    @server.tool()
    def confirm_remote_annotation_request(context_id: str, request_key: str, annotation_id: str, revision: int) -> dict:
        """제품이 동일 맥락·ID·revision의 응답 수신을 검증한 후 확정한다. false면 현재 결과를 재조회한다."""
        return call(True, lambda owner: {'confirmed': service.confirm(owner, context(context_id), request_key, context(annotation_id), revision)})

    from .remote_annotation_cards import register_annotation_cards
    register_annotation_cards(server, service, call, context, binding)
