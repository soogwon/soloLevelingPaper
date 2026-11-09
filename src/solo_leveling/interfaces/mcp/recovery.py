"""#222 서비스만 사용하는 복구 접수·조회 및 백그라운드 수명 관리 (#223)."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from importlib.resources import files
import json
import threading
from typing import Annotated, Literal
from typing_extensions import TypedDict

import anyio
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from .errors import to_tool_error
from .ingestion import IngestionShuttingDownError
from .registration_consent import RegistrationGate


RECOVERY_URI = 'ui://solo-leveling/translation-recovery.html'
NOTICE = ('실패한 청크의 원문과 대상 언어를 기존 번역 제공자에 다시 전송할 수 있으며 추가 API 비용이 발생할 수 있습니다. '
          '현재 MCP 런타임의 번역 제공자는 OpenAI이며, 동의 후 백그라운드에서 전송합니다. '
          '동의해도 LOCAL_ONLY와 외부 API 차단 설정은 유지됩니다. 기존 성공 번역과 학습 맥락은 바꾸지 않습니다. '
          '응답이 없으면 같은 요청 키로 상태를 조회하고 새 작업을 자동 접수하지 마세요.')


CARD_GUIDANCE = ('사용자 화면의 카드 표시 여부는 이 도구 응답으로 확인할 수 없습니다. '
                 '카드가 보인다거나 보이지 않는다고 단정하지 말고 복구 준비 상태를 안내하세요. '
                 '카드가 보이면 사용자가 직접 확인하도록 안내하고, 대신 접수하지 마세요.')


class RecoveryOutput(TypedDict):
    status: Literal['ACCEPTED', 'IN_PROGRESS', 'COMPLETED', 'FAILED', 'INTERRUPTED', 'UNKNOWN',
                    'NO_TARGET', 'BASE_CHANGED', 'REQUEST_CONFLICT']
    version_id: str
    base_embedding_set_id: str
    job_id: str | None
    new_embedding_set_id: str | None
    outcome: Literal['FULL', 'PARTIAL', 'NO_PROGRESS'] | None
    reason_code: str | None
    target_count: int | None
    recovered_count: int | None
    remaining_failed_count: int | None
    result_available: bool


class RecoveryPreview(TypedDict):
    status: Literal['AVAILABLE', 'NO_TARGET', 'BASE_CHANGED', 'UNAVAILABLE']
    target_count: int | None
    translated_count: int | None
    notice: str
    version_id: str
    base_embedding_set_id: str | None
    request_key: str


class CancelledOutput(TypedDict):
    status: Literal['cancelled']


class RecoveryManager:
    def __init__(self, service):
        self.service = service
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='translation-recovery')
        self.condition = threading.Condition(threading.RLock())
        self.pending = {}
        self.accepting = True

    def preview(self, version_id, base_embedding_set_id, request_key):
        return self.service.preview(version_id, base_embedding_set_id, request_key)

    def lookup(self, version_id, base_embedding_set_id, request_key):
        return asdict(self.service.lookup(version_id, base_embedding_set_id, request_key))

    def submit(self, version_id, base_embedding_set_id, request_key):
        with self.condition:
            if not self.accepting:
                raise IngestionShuttingDownError()
            result = self.service.prepare(version_id, base_embedding_set_id, request_key)
            if result.status == 'ACCEPTED':
                try:
                    if len(self.pending) >= 16:
                        raise RuntimeError('RECOVERY_QUEUE_FULL')
                    future = self.executor.submit(self.service.run, result.job_id)
                    self.pending[future] = result.job_id
                    future.add_done_callback(self._done)
                except Exception:
                    self.service.cancel_before_start(result.job_id)
                    return self.lookup(version_id, base_embedding_set_id, request_key)
            return asdict(result)

    def _done(self, future):
        try:
            if not future.cancelled():
                future.exception()
        finally:
            with self.condition:
                self.pending.pop(future, None)
                self.condition.notify_all()

    def begin_shutdown(self):
        with self.condition:
            if not self.accepting:
                return
            self.accepting = False
            for future, job_id in list(self.pending.items()):
                if future.cancel():
                    self.service.cancel_before_start(job_id)
            self.executor.shutdown(wait=False, cancel_futures=False)

    def wait_for_idle(self, timeout):
        with self.condition:
            return self.condition.wait_for(lambda: not self.pending, timeout)


def register_recovery(server, manager):
    gate = RegistrationGate()

    @server.resource(RECOVERY_URI, name='translation-recovery', title='번역 복구 전 확인',
        mime_type='text/html;profile=mcp-app',
        meta={'ui': {'csp': {'connectDomains': [], 'resourceDomains': []}}})
    def recovery_html() -> str:
        return files('solo_leveling.interfaces.mcp').joinpath('assets/translation-recovery.html').read_text(encoding='utf-8')

    def scope(version_id, base_embedding_set_id):
        return json.dumps([version_id, base_embedding_set_id], ensure_ascii=False)

    @server.tool(name='prepare_translation_recovery', structured_output=True,
        meta={'ui': {'resourceUri': RECOVERY_URI}},
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    async def prepare_translation_recovery(version_id: str, request_key: str, base_embedding_set_id: str | None = None) -> Annotated[CallToolResult, RecoveryPreview]:
        """복구 대상 수와 외부 전송·비용 안내를 표시합니다. 아직 번역이나 작업을 시작하지 않습니다.

        사용자의 명시적 복구 요청이 있을 때만 안내하세요. 질문·조회·재등록은 동의가 아닙니다.
        base_embedding_set_id 생략 시 조회한 현재 기본 색인을 카드에 고정합니다. 이후 접수·조회는 그 ID를 사용하세요.
        카드에서 직접 동의해야 접수됩니다. 응답만으로 카드 표시 여부를 추측하지 말고 준비 상태를 안내하세요.
        """
        try:
            preview = await anyio.to_thread.run_sync(lambda: manager.preview(version_id, base_embedding_set_id, request_key))
            data = {**preview, 'notice': NOTICE, 'version_id': version_id, 'request_key': request_key}
            meta = ({'confirmation_token': gate.prepare(scope(version_id, preview['base_embedding_set_id']), request_key)}
                    if preview['status'] == 'AVAILABLE' else {})
            return CallToolResult(
                content=[TextContent(type='text', text=json.dumps(data, ensure_ascii=False)),
                         TextContent(type='text', text=CARD_GUIDANCE)],
                structuredContent=data, _meta=meta,
            )
        except Exception as error:
            raise to_tool_error(error) from None

    @server.tool(name='recover_translation', structured_output=True,
        meta={'ui': {'visibility': ['app']}},
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
    async def recover_translation(version_id: str, base_embedding_set_id: str, request_key: str,
                                  confirmation_token: str) -> RecoveryOutput:
        """카드에서 사용자 동의 후에만 복구를 접수합니다. 모델이 대신 호출하지 마세요.

        confirmed=true는 승인 증명이 아닙니다. 응답 유실 시 같은 키로 조회하며 새 키로 재접수하지 마세요.
        """
        try:
            gate.consume(confirmation_token, scope(version_id, base_embedding_set_id), request_key)
        except ToolError:
            raise ToolError('RECOVERY_CONFIRMATION_REQUIRED: 복구 안내 카드에서 다시 확인하세요.') from None
        try:
            return await anyio.to_thread.run_sync(lambda: manager.submit(version_id, base_embedding_set_id, request_key))
        except Exception as error:
            raise to_tool_error(error) from None

    @server.tool(name='cancel_translation_recovery', structured_output=True,
        meta={'ui': {'visibility': ['app']}},
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def cancel_translation_recovery(confirmation_token: str) -> CancelledOutput:
        """접수 전 카드 동의를 취소합니다. 이미 접수된 실행을 중단하는 도구가 아닙니다."""
        gate.cancel(confirmation_token)
        return {'status': 'cancelled'}

    @server.tool(name='get_translation_recovery', structured_output=True,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def get_translation_recovery(version_id: str, base_embedding_set_id: str, request_key: str) -> RecoveryOutput:
        """접수 전에 보관한 같은 키·버전·기준 색인으로 복구 상태를 조회합니다. 새 작업은 생성하지 않습니다.

        COMPLETED의 FULL/PARTIAL, 실패 NO_PROGRESS, INTERRUPTED, 진행 중과 UNKNOWN을 구분하세요.
        UNKNOWN은 실패 확정이 아닙니다. 완료돼도 기존 context를 바꾸지 말고 새 학습 선택을 물어보세요.
        """
        try:
            return await anyio.to_thread.run_sync(lambda: manager.lookup(version_id, base_embedding_set_id, request_key))
        except Exception as error:
            raise to_tool_error(error) from None
