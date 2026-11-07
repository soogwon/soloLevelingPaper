"""메모 서비스의 저장·조회 계약을 민감정보 없는 MCP 오류로 연결한다."""
from dataclasses import asdict
from functools import partial
from importlib.resources import files
from typing import Literal

import anyio
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from typing_extensions import TypedDict
from pydantic import BaseModel, ConfigDict, Field

from solo_leveling.application.annotations.service import AnnotationSource
from solo_leveling.domain.annotation import (
    Annotation, AnnotationResult, AnnotationOutcome, AnnotationValidationError, SegmentInput,
)
from .errors import to_tool_error


class MutationOutput(TypedDict):
    outcome: Literal['saved', 'deleted']
    annotation: Annotation | None
    code: str | None


class DetailOutput(TypedDict):
    source: AnnotationSource | None
    annotation: Annotation
    segment_available: list[bool]
    location_status: Literal['available', 'unavailable', 'not_applicable']


class PageOutput(TypedDict):
    items: list[Annotation]
    next_cursor: str | None


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    context_id: str


class Segment(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    chunk_id: str
    start_offset: int
    end_offset: int
    quote_snapshot: str
    target: Literal['original', 'translation'] = 'original'
    translation_revision_id: str | None = None


class CreateInput(Input):
    kind: Literal['note', 'highlight']
    body: str
    segments: list[Segment] = Field(default_factory=list)
    step: str | None = None


class GetInput(Input):
    annotation_id: str


class DetailInput(GetInput):
    source_position: int | None = Field(default=None, ge=0)


class ListInput(Input):
    step: str | None = None
    kind: str | None = None
    limit: int | None = None
    cursor: str | None = None


class DeleteInput(GetInput):
    expected_revision: int


class UpdateInput(DeleteInput):
    body: str | None = None
    step: str | None = None
    segments: list[Segment] | None = None


ANNOTATION_INPUTS = {
    'create_annotation': CreateInput, 'get_annotation': DetailInput,
    'list_annotations': ListInput, 'update_annotation': UpdateInput,
    'delete_annotation': DeleteInput,
}
_SAFE_REASONS = frozenset({
    'TRANSLATION_NOT_SUPPORTED', 'INVALID_LIMIT', 'INVALID_CURSOR', 'DUPLICATE_SEGMENT',
    'QUOTE_TOTAL_TOO_LONG', 'CHUNK_NOT_IN_CONTEXT', 'TRANSLATION_REVISION_MISMATCH',
    'OFFSET_OUT_OF_RANGE', 'QUOTE_MISMATCH',
})


def invalid(code):
    reason = code if code in _SAFE_REASONS else 'INVALID_INPUT'
    return ToolError(f'INVALID_ARGUMENT: {reason}: 입력값을 확인해주세요.')


def original_segments(segments):
    if any(item.target != 'original' for item in segments):
        raise invalid('TRANSLATION_NOT_SUPPORTED')
    try:
        return tuple(SegmentInput(**item.model_dump()) for item in segments)
    except AnnotationValidationError as error:
        raise invalid(error.code) from None


async def invoke(method, *args, **kwargs):
    try:
        value = await anyio.to_thread.run_sync(partial(method, *args, **kwargs))
    except AnnotationValidationError as error:
        raise invalid(error.code) from None
    except Exception as error:
        raise to_tool_error(error) from None
    if isinstance(value, AnnotationResult):
        if value.outcome == AnnotationOutcome.CONFLICT:
            raise ToolError('CONFLICT: 기록을 다시 조회한 뒤 수정해주세요. 자동 재시도하지 마세요.')
        if value.outcome == AnnotationOutcome.NOT_FOUND or value.code == 'CONTEXT_NOT_FOUND':
            raise ToolError('NOT_FOUND: 요청한 자료를 찾을 수 없습니다.')
        if value.outcome == AnnotationOutcome.INVALID_REFERENCE:
            raise invalid(value.code)
    return asdict(value)


ANNOTATION_URI = 'ui://solo-leveling/annotation.html'


def register_annotations(server, service):
    @server.resource(ANNOTATION_URI, name='annotation', title='메모와 원문 위치',
                     mime_type='text/html;profile=mcp-app',
                     meta={'ui': {'csp': {'connectDomains': [], 'resourceDomains': []}}})
    def annotation_html() -> str:
        return files('solo_leveling.interfaces.mcp').joinpath('assets/annotation.html').read_text(encoding='utf-8')

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                            idempotentHint=False, openWorldHint=False))
    async def create_annotation(request: CreateInput) -> MutationOutput:
        """메모·하이라이트를 저장합니다. 원문 위치는 문자 단위 [시작, 끝)이며 인용문과 일치해야 합니다.
        생성 응답을 잃으면 목록을 먼저 조회하세요. 자동 재생성은 중복 기록을 만들 수 있습니다.
        """
        segments = original_segments(request.segments)
        return await invoke(service.create, request.context_id, request.kind, request.body,
                            segments=segments, step=request.step)

    @server.tool(meta={'ui': {'resourceUri': ANNOTATION_URI}},
                 annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def get_annotation(request: DetailInput) -> DetailOutput:
        """저장 기록과 구간별 위치 확인 결과를 조회합니다.
        원문을 열려면 source_position에 저장된 구간의 position을 전달하세요.
        segment_available이 참인 구간만 원문 이동에 사용하세요. 거짓이면 이동 불가로 안내하세요.
        """
        detail = await invoke(service.get, request.context_id, request.annotation_id, source_position=request.source_position)
        available = detail['segment_available']
        detail['location_status'] = ('not_applicable' if not available else
                                     'available' if all(available) else 'unavailable')
        return detail

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def list_annotations(request: ListInput) -> PageOutput:
        """기본 20개, 최대 50개 기록을 조회합니다. next_cursor는 같은 context와 필터로 전달하세요.
        원문으로 이동하기 전 get_annotation으로 현재 위치 유효성을 확인하세요.
        """
        return await invoke(service.list, **request.model_dump())

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                            idempotentHint=False, openWorldHint=False))
    async def update_annotation(request: UpdateInput) -> MutationOutput:
        """조회한 revision을 expected_revision으로 전달해 본문·학습 단계를 수정합니다.
        원문 위치는 고정입니다. 충돌하면 재조회하고 자동 재시도하지 마세요. step=null은 단계 해제입니다.
        """
        changes = request.model_dump(exclude_unset=True, exclude={'context_id', 'annotation_id', 'expected_revision'})
        if 'segments' in changes:
            if request.segments is None:
                raise invalid(None)
            changes['segments'] = original_segments(request.segments)
        return await invoke(service.update, request.context_id, request.annotation_id,
                            request.expected_revision, **changes)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                            idempotentHint=False, openWorldHint=False))
    async def delete_annotation(request: DeleteInput) -> MutationOutput:
        """지정한 revision의 기록을 삭제합니다. 충돌하면 재조회하고 자동 재시도하지 마세요."""
        return await invoke(service.delete, **request.model_dump())
