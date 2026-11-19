"""요약 준비·기존 결과 조회 도구. 동의는 인증 웹에서만 기록한다."""
import time
from mcp.types import ToolAnnotations
from mcp.server.auth.middleware.auth_context import get_access_token
from solo_leveling.interfaces.http.remote_mcp_auth import RemoteIdentityAccessToken
from solo_leveling.interfaces.http.remote_executions import RemoteExecutionConflict


def register_summary_tools(server, queries, reviews):
    def call(method, *args, **kwargs):
        token = get_access_token()
        if (not isinstance(token, RemoteIdentityAccessToken) or token.verified_identity() is None
                or type(token.expires_at) is not int or token.expires_at <= time.time()
                or 'summaries:write' not in token.scopes):
            return {'status': 'not_authorized'}
        owner = queries.owner()
        if owner is None:
            return {'status': 'not_authorized'}
        try:
            result = getattr(reviews, method)(owner, *args, **kwargs)
            return {'status': 'not_found'} if result is None else result
        except RemoteExecutionConflict:
            return {'status': 'conflict', 'message': '같은 요청 키의 요약 범위를 변경할 수 없습니다.'}
        except ValueError:
            return {'status': 'review_required', 'message': '인증 웹에서 요약 동의와 상태를 확인하세요. 자동 재제출하지 마세요.'}
        except Exception:
            return {'status': 'unavailable', 'message': '같은 요청 키로 다시 조회하세요. 자동 생성하지 마세요.'}

    @server.tool()
    def prepare_remote_summary(request_key: str, context_id: str, scope: str = 'paper', section_id: str | None = None) -> dict:
        """요약 검토를 준비하고 UUID 키를 보관한다. AI 호출 없이 웹에서 저장·AI 전송 동의를 각각 받아야 한다."""
        return call('prepare', request_key, context_id, scope=scope, section_id=section_id)

    @server.tool()
    def get_remote_summary_review(request_key: str) -> dict:
        """같은 요약 요청의 전송 범위·동의·접수 상태를 조회한다. 새 생성이나 재접수를 하지 않는다."""
        return call('view', request_key)

    @server.tool()
    def submit_remote_summary(request_key: str) -> dict:
        """웹에서 두 목적 동의를 마친 기존 요청만 접수한다. 모델의 동의 주장은 허용하지 않는다."""
        return call('submit', request_key)

    @server.tool()
    def get_remote_summary_result(request_key: str) -> dict:
        """같은 요청 키의 저장 요약과 근거 ID를 재조회한다. 새 생성·외부 호출은 하지 않는다."""
        return call('result', request_key)

    @server.tool()
    def cancel_remote_summary(request_key: str) -> dict:
        """사용자가 요청한 기존 요약을 취소한다. 이미 전송한 호출의 비용은 취소되지 않는다."""
        return call('cancel', request_key)

    @server.tool()
    def get_remote_summary_evidence(request_key: str, evidence_id: str) -> dict:
        """저장된 요약이 실제 인용한 근거의 고정 원문·번역·페이지를 재조회한다. 새 검색·생성은 없다."""
        return call('evidence', request_key, evidence_id)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    def get_latest_remote_summary(context_id: str) -> dict:
        """해당 맥락의 가장 최근 성공 요약을 읽는다. 실패한 재생성은 이전 성공 요약을 덮어쓰지 않는다."""
        return call('latest', context_id)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    def export_remote_summary(request_key: str) -> dict:
        """같은 요청 키의 완료 요약을 판본·범위·근거 ID와 함께 Markdown으로 내보낸다. 생성·수정은 없다."""
        saved = call('result', request_key)
        if saved.get('state') != 'completed' or not saved.get('result'):
            return saved
        from solo_leveling.application.summary.remote_export import export_summary
        try:
            return export_summary(saved)
        except (ValueError, KeyError, TypeError):
            return {'status': 'unavailable', 'message': '저장 요약을 다시 조회하세요.'}
