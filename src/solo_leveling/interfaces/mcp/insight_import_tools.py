"""로컬 저장 항목의 선택·검토·전송을 MCP 앱에 연결한다."""
from importlib.resources import files
from uuid import UUID

import anyio
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from .confirmations import ConfirmationStore

URI = 'ui://solo-leveling/insight-import.html'
SELECTION = 'insight_import_selection'
SUBMISSION = 'insight_import_submission'
APP_ONLY = {'ui': {'visibility': ['app']}}


def _card(message, **data):
    return CallToolResult(content=[TextContent(type='text', text=message)], _meta=data)


def register_insight_import(server, service, confirmations):
    store = confirmations if confirmations is not None else ConfirmationStore()

    @server.resource(URI, name='insight-import', title='저장 키워드·중요 문장 이관',
                     mime_type='text/html;profile=mcp-app',
                     meta={'ui': {'csp': {'connectDomains': [], 'resourceDomains': []}}})
    def html() -> str:
        return files('solo_leveling.interfaces.mcp').joinpath('assets/insight-import.html').read_text(encoding='utf-8')

    @server.tool(meta={'ui': {'resourceUri': URI}}, annotations=ToolAnnotations(readOnlyHint=True))
    async def open_paper_insight_import(context_id: str, remote_context_id: UUID,
                                        scope: str = 'paper') -> CallToolResult:
        """로컬 학습 맥락과 대상 원격 학습 맥락을 지정해 저장 항목 선택 카드를 엽니다. 섹션이면 scope에 섹션 ID를 지정하세요."""
        try:
            selection = await anyio.to_thread.run_sync(service.list_items, context_id, scope, str(remote_context_id))
            connection = selection.pop('connection')
            token = store.issue(SELECTION, context_id, {
                **{key: value for key, value in selection.items() if key != 'items'},
                'connection': connection,
            })
        except Exception:
            return _card('저장 항목을 불러오지 못했습니다. 로컬 학습 맥락과 저장 범위를 확인하세요.')
        return _card('카드에서 저장 항목을 선택하고 검토하세요. 채팅에서는 이관을 제출할 수 없습니다.',
                     selection=selection, selection_token=token)

    @server.tool(meta=APP_ONLY, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
    async def preview_paper_insight_import(context_id: str, selection_token: str,
                                           insight_ids: list[str]) -> CallToolResult:
        """선택한 저장 항목의 해시를 대조하고 전송할 내용과 고정 판본을 카드에 표시합니다."""
        accepted, selection = store.take(SELECTION, selection_token, context_id, keep_confirmed=True)
        if not accepted:
            accepted, selection = store.lookup_confirmed(SELECTION, selection_token, context_id)
        if not accepted:
            return _card('선택 카드가 만료되었습니다. 다시 열어 주세요.')
        try:
            review, snapshot = await anyio.to_thread.run_sync(service.prepare, selection, insight_ids)
            token = store.issue(SUBMISSION, context_id, snapshot)
        except Exception:
            return _card('미리보기를 만들지 못했습니다. 서버 연결, 원격 판본, 선택 항목의 출처를 확인하세요.')
        return _card('카드에서 전송 범위와 대상 판본을 확인하세요.', review=review, submission_token=token)

    @server.tool(meta=APP_ONLY, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                                           idempotentHint=True, openWorldHint=True))
    async def submit_paper_insight_import(context_id: str, submission_token: str) -> CallToolResult:
        """확인한 스냅샷을 같은 요청 키로 제출합니다."""
        accepted, snapshot = store.take(SUBMISSION, submission_token, context_id, keep_confirmed=True)
        if not accepted:
            accepted, snapshot = store.lookup_confirmed(SUBMISSION, submission_token, context_id)
        if not accepted:
            return _card('제출 확인이 만료되었습니다. 미리보기를 다시 여세요.')
        try:
            receipt = await anyio.to_thread.run_sync(service.submit, snapshot)
        except ValueError:
            return _card('제출이 거부되었습니다. 판본 또는 기존 이관 상태를 확인하세요.', rejected=True)
        except Exception:
            return _card('결과를 확인하지 못했습니다. 같은 확인으로 다시 제출할 수 있습니다.')
        return _card('원격 판본에 저장했습니다.', receipt=receipt)

    @server.tool(meta=APP_ONLY, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                                           idempotentHint=True, openWorldHint=False))
    def cancel_paper_insight_import(submission_token: str) -> CallToolResult:
        """미리보기의 제출 확인을 폐기합니다."""
        store.cancel(SUBMISSION, submission_token)
        return _card('제출 확인을 폐기했습니다.')
