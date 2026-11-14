"""SDK의 입출력 검증 오류에서 본문이 노출되지 않도록 보호한다."""

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolRequest, CallToolResult, ServerResult, TextContent
from pydantic import ValidationError
from jsonschema import validate, ValidationError as SchemaValidationError
from uuid import UUID

from solo_leveling.application.evidence_qa.errors import InvalidArgumentError
from .errors import invalid_input, registration_input_error, to_tool_error


def model_error_field(error: ValidationError, fields, prefix: str = '') -> str | None:
    """Pydantic 오류에서 공개 스키마에 있는 첫 필드 이름만 꺼낸다. 입력값은 읽지 않는다."""
    for issue in error.errors(include_url=False, include_context=False, include_input=False):
        location = issue.get('loc', ())
        if location and location[0] in fields:
            return f'{prefix}{location[0]}'
    return None


def schema_error_field(error: SchemaValidationError, schema: dict) -> str | None:
    """JSON 스키마 오류에서 공개 필드 이름만 꺼낸다. 알 수 없는 키 이름은 사용자 입력이라 넣지 않는다."""
    properties = set(schema.get('properties', {}))
    path = list(error.absolute_path)
    if path and path[0] in properties:
        return path[0]
    if error.validator == 'required' and isinstance(error.instance, dict):
        missing = [name for name in schema.get('required', []) if name not in error.instance]
        if missing and missing[0] in properties:
            return missing[0]
    return None


def is_malformed_context_id(arguments) -> bool:
    """학습 맥락 ID는 UUID로 발급된다. 없는 ID 중 형식부터 틀린 값은 입력 오류로 구분한다."""
    value = arguments.get('context_id') if isinstance(arguments, dict) else None
    if not isinstance(value, str):
        return False
    try:
        UUID(value)
    except ValueError:
        return True
    return False


class SafeInputServer(FastMCP):
    """SDK 입력 모델을 공유하고 잘못된 입력은 고정 오류로 반환한다."""

    # 학습 맥락 저장소(get_context). 있으면 없는 맥락 중 형식이 틀린 ID를 입력 오류로 구분한다.
    context_reader = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        handler = self._mcp_server.request_handlers[CallToolRequest]

        async def safe_handler(request):
            # SDK는 미등록 도구 이름을 경고 로그에 넣으므로 그보다 먼저 차단한다.
            try:
                self._find_tool(request.params.name)
            except ToolError as error:
                return ServerResult(CallToolResult(isError=True, content=[TextContent(
                    type='text', text=str(error))]))
            return await handler(request)

        self._mcp_server.request_handlers[CallToolRequest] = safe_handler

    def _find_tool(self, name):
        tool = self._tool_manager.get_tool(name)
        if tool is None:
            # 도구 이름도 호출자가 제공한 데이터이므로 오류에 다시 넣지 않는다.
            raise to_tool_error(InvalidArgumentError('잘못된 도구입니다.')) from None
        return tool

    @staticmethod
    def _invalid_input(error: Exception, tool=None) -> ToolError:
        """입력값을 노출하지 않고 공개 스키마의 필드명만 오류에 포함한다."""
        if tool is not None and tool.name == 'prepare_paper_registration':
            field = None
            if isinstance(error, ValidationError):
                # 알려진 필드 경로만 허용한다. 추가 키 이름과 입력값은 노출하지 않는다.
                allowed = {'source', 'source.kind', 'source.relative_path', 'request_key'}
                for issue in error.errors(include_url=False, include_context=False, include_input=False):
                    path = '.'.join(str(part) for part in issue.get('loc', ()))
                    if path in allowed:
                        field = path
                        break
            return registration_input_error(field)
        if not isinstance(error, ValidationError):
            return invalid_input(reason='INVALID_JSON')
        fields = set(tool.parameters.get('properties', {})) if tool is not None else set()
        return invalid_input(model_error_field(error, fields))

    async def _missing_malformed_context(self, arguments) -> bool:
        """UUID가 아니면서 저장되지 않은 context_id인지 본다. 저장된 레거시 ID는 그대로 둔다."""
        if self.context_reader is None or not is_malformed_context_id(arguments):
            return False
        context_id = arguments['context_id']
        try:
            found = await anyio.to_thread.run_sync(lambda: self.context_reader.get_context(context_id))
        except Exception:
            return False
        return found is None

    async def call_tool(self, name, arguments):
        tool = self._find_tool(name)
        try:
            # SDK의 JSON 문자열 변환·기본값·타입 검증을 그대로 재사용한다.
            # 서비스나 출력 검증은 실행하지 않으므로 입력 오류와 혼동하지 않는다.
            parsed = tool.fn_metadata.pre_parse_json(arguments)
            tool.fn_metadata.arg_model.model_validate(parsed)
        except ValueError as error:
            # JSON 정수 변환 한도 초과와 Pydantic 검증 실패를 같은 입력 오류로 처리한다.
            raise self._invalid_input(error, tool) from None
        try:
            result = await super().call_tool(name, arguments)
        except ToolError as error:
            # 입력 검사를 통과한 뒤 SDK 출력 검증·직렬화가 실패한 경우다.
            # 기존 서비스의 고정 오류 코드는 그대로 유지한다.
            if isinstance(error.__cause__, ToolError):
                public = error.__cause__
                # 없음이나 필드 없는 입력 오류가 형식이 틀린 미저장 context_id 때문이면 필드를 알려 준다.
                vague = str(public).startswith('NOT_FOUND:') or str(public) == str(invalid_input())
                if vague and await self._missing_malformed_context(arguments):
                    raise invalid_input('context_id') from None
                raise public from None
            raise to_tool_error(RuntimeError()) from None

        if tool.output_schema is not None:
            # 하위 프로토콜 계층도 출력 JSON 스키마를 검사한다. 그 계층이
            # 원문 포함 오류를 만들기 전에 같은 검사를 안전하게 수행한다.
            if isinstance(result, CallToolResult):
                structured = result.structuredContent
            elif isinstance(result, tuple) and len(result) == 2:
                structured = result[1]
            elif isinstance(result, dict):
                structured = result
            else:
                structured = None
            try:
                validate(structured, tool.output_schema)
            except SchemaValidationError:
                raise to_tool_error(RuntimeError()) from None
        return result
