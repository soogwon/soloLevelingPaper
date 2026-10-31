"""SDK의 입출력 검증 오류에서 본문이 노출되지 않도록 보호한다."""

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolRequest, CallToolResult, ServerResult, TextContent
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError
from jsonschema import validate, ValidationError as SchemaValidationError

from solo_leveling.application.evidence_qa.errors import InvalidArgumentError
from .errors import to_tool_error


class SafeInputServer(FastMCP):
    """SDK 입력 모델을 공유하고 잘못된 입력은 고정 오류로 반환한다."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        handler = self._mcp_server.request_handlers[CallToolRequest]

        async def safe_handler(request):
            # SDK는 미등록 도구 이름을 경고 로그에 넣으므로 그보다 먼저 차단한다.
            try:
                self._find_tool(request.params.name)
            except ToolError:
                return ServerResult(CallToolResult(isError=True, content=[TextContent(
                    type='text', text='INVALID_ARGUMENT: 입력값을 확인해주세요.')]))
            return await handler(request)

        self._mcp_server.request_handlers[CallToolRequest] = safe_handler

    def _find_tool(self, name):
        tool = self._tool_manager.get_tool(name)
        if tool is None:
            # 도구 이름도 호출자가 제공한 데이터이므로 오류에 다시 넣지 않는다.
            raise to_tool_error(InvalidArgumentError('잘못된 도구입니다.')) from None
        return tool

    async def call_tool(self, name, arguments):
        tool = self._find_tool(name)
        try:
            # SDK의 JSON 문자열 변환·기본값·타입 검증을 그대로 재사용한다.
            # 서비스나 출력 검증은 실행하지 않으므로 입력 오류와 혼동하지 않는다.
            parsed = tool.fn_metadata.pre_parse_json(arguments)
            tool.fn_metadata.arg_model.model_validate(parsed)
        except ValidationError:
            # 검증 예외에는 본문이 있으므로 로그나 예외 연결로 전달하지 않는다.
            raise to_tool_error(InvalidArgumentError('잘못된 입력입니다.')) from None
        try:
            result = await super().call_tool(name, arguments)
        except ToolError as error:
            # 입력 검사를 통과한 뒤 SDK 출력 검증·직렬화가 실패한 경우다.
            # 기존 서비스의 고정 오류 코드는 그대로 유지한다.
            if isinstance(error.__cause__, (ValidationError, PydanticSerializationError)):
                raise to_tool_error(RuntimeError()) from None
            raise

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
