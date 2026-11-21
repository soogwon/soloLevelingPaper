"""요약 준비·기존 결과 조회 도구. 동의는 인증 웹(가입 동의 포함)에서만 기록한다."""
import time
from typing import Literal
from typing_extensions import TypedDict
from mcp.types import ToolAnnotations
from mcp.server.auth.middleware.auth_context import get_access_token
from solo_leveling.application.summary.remote_contract import RemoteSummaryError
from solo_leveling.interfaces.http.remote_mcp_auth import RemoteIdentityAccessToken
from solo_leveling.interfaces.http.remote_executions import RemoteExecutionConflict
from solo_leveling.interfaces.http.remote_summary_intake import SUMMARY_ADMISSION_MESSAGES, SummaryAdmissionDenied


class ChatSummaryItem(TypedDict):
    kind: Literal['claim', 'method', 'result']
    text: str
    chunk_ids: list[str]


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
        except SummaryAdmissionDenied as error:
            return {'status': error.code, 'message': SUMMARY_ADMISSION_MESSAGES[error.code]}
        except RemoteSummaryError as error:
            if str(error) == 'REMOTE_SUMMARY_SOURCE_TOO_LARGE':
                return {'status': 'summary_source_too_large',
                        'message': '현재 입력 한도로 요약을 준비할 수 없어요. 더 작은 섹션을 선택하세요.'}
            return {'status': 'review_required', 'message': '인증 웹에서 요약 동의와 상태를 확인하세요. 자동 재제출하지 마세요.'}
        except ValueError as error:
            if str(error) == 'chat_summary_evidence_invalid':
                return {'status': 'invalid_evidence', 'message': '현재 학습 맥락의 PDF 본문에서 근거 청크를 다시 확인하세요.'}
            if method == 'prepare_chat':
                return {'status': 'invalid_argument', 'message': '항목별 한 문장과 근거 청크 ID를 확인하세요.'}
            return {'status': 'review_required', 'message': '인증 웹에서 요약 동의와 상태를 확인하세요. 자동 재제출하지 마세요.'}
        except Exception:
            return {'status': 'unavailable', 'message': '같은 요청 키로 다시 조회하세요. 자동 생성하지 마세요.'}

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    def prepare_remote_chat_summary(request_key: str, context_id: str, items: list[ChatSummaryItem]) -> dict:
        """근거를 확인해 작성한 채팅 요약을 웹 저장 검토용으로 준비한다. 별도 저장 요청을 기다리지 않는다.
        PDF 페이지 본문 조회에서 받은 chunk_id로 각 항목의 근거를 지정한다. claim은 연구 질문, method는 핵심 접근, result는 주요 결과다.
        항목별 한국어 한 문장을 전달하고 근거가 없는 항목은 생략한다. 같은 요청에는 같은 UUID를 쓴다.
        status가 saved이면 계정 저장 동의로 이미 저장됐으니 review_url을 인용 확인·저장 취소 링크로 보여준다.
        review_required이면 같은 답변에 ‘인용 확인하고 저장’ 링크를 보여준다. 외부 AI 호출은 없다."""
        return call('prepare_chat', request_key, context_id, items)

    @server.tool()
    def prepare_remote_summary(request_key: str, context_id: str, scope: str = 'paper', section_id: str | None = None) -> dict:
        """서버 생성 전용이다. 사용자가 서버 생성을 명시적으로 요청한 경우에만 쓴다.
        일반 요약 요청은 Claude 채팅에서 답하고 prepare_remote_chat_summary로 저장한다.
        가입 때 저장·OpenAI 전송에 동의한 계정이면 바로 접수하고(status submitted) 아니면 웹 검토 링크를 준다.
        비용은 운영자가 부담하며 서버 예산 한도를 넘으면 거절된다."""
        return call('prepare', request_key, context_id, scope=scope, section_id=section_id)

    @server.tool()
    def get_remote_summary_review(request_key: str) -> dict:
        """같은 요약 요청의 전송 범위·동의·접수 상태를 조회한다. 새 생성이나 재접수를 하지 않는다."""
        return call('view', request_key)

    @server.tool()
    def submit_remote_summary(request_key: str) -> dict:
        """웹 검토에서 두 목적에 동의한 기존 요청만 접수한다. 계정 동의로 이미 접수된 요청은 기존 상태를 돌려준다.
        동의 완료 알림이나 요약 상태 확인만 요청받으면 조회 도구를 쓰고 제출하지 않는다."""
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

    return get_latest_remote_summary
