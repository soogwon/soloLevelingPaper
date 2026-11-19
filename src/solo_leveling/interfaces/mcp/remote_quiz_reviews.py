"""퀴즈 생성 검토 링크와 상태만 제공한다. 동의·접수는 인증 웹에서 수행한다."""
import time
from mcp.server.auth.middleware.auth_context import get_access_token
from solo_leveling.interfaces.http.remote_mcp_auth import RemoteIdentityAccessToken
from solo_leveling.interfaces.http.remote_executions import RemoteExecutionConflict


def register_quiz_review_tools(server, queries, reviews):
    def call(method, *args, **kwargs):
        token = get_access_token()
        if (not isinstance(token, RemoteIdentityAccessToken) or token.verified_identity() is None
                or type(token.expires_at) is not int or token.expires_at <= time.time()
                or 'quizzes:write' not in token.scopes):
            return {'status': 'not_authorized'}
        owner = queries.owner()
        if owner is None:
            return {'status': 'not_authorized'}
        try:
            result = getattr(reviews, method)(owner, *args, **kwargs)
            return {'status': 'not_found'} if result is None else result
        except RemoteExecutionConflict:
            return {'status': 'conflict', 'message': '같은 요청 키의 퀴즈 범위를 변경할 수 없습니다.'}
        except ValueError:
            return {'status': 'review_required', 'message': '인증 웹에서 퀴즈 생성 범위와 기한을 확인하세요.'}
        except Exception:
            return {'status': 'unavailable', 'message': '같은 요청 키로 다시 조회하세요. 자동 생성하지 마세요.'}

    @server.tool()
    def prepare_remote_quiz_generation(request_key: str, context_id: str, evidence_ids: list[str],
                                      step: str, question_count: int, question_type: str) -> dict:
        """저장된 인용으로 검토 링크를 준비한다. 생성이나 동의는 하지 않으며 웹에서 두 목적을 각각 확인해야 한다."""
        return call('prepare', request_key, context_id, tuple(evidence_ids), step=step,
                    question_count=question_count, question_type=question_type)

    @server.tool()
    def get_remote_quiz_generation_review(request_key: str) -> dict:
        """같은 요청의 범위·동의·접수 상태만 다시 조회한다. 만료 후에도 새 생성 없이 기존 상태를 확인한다."""
        return call('view', request_key)
