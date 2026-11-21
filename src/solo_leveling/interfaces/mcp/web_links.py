"""원격 MCP 응답의 웹 경로(`/#/...`)에 같은 이름의 전체 URL을 덧붙인다.

Claude는 서버 도메인을 모르므로 경로만 받으면 바로 열 수 있는 링크를 줄 수 없다.
기존 `*web_path` 키는 호환을 위해 그대로 두고, 옆에 `*web_url`을 추가한다.
"""
from __future__ import annotations

import functools
import inspect
from typing import Any
from urllib.parse import urlsplit


def web_url_key(key: str) -> str | None:
    """`web_path`→`web_url`, `review_web_path`→`review_web_url`. 해당 없으면 None."""
    return key[:-len('web_path')] + 'web_url' if key.endswith('web_path') else None


def with_web_urls(value: Any, origin: str) -> Any:
    """dict·list를 재귀로 따라가며 `/#/` 경로 옆에 전체 URL을 넣은 사본을 돌려준다."""
    if isinstance(value, dict):
        result = {key: with_web_urls(item, origin) for key, item in value.items()}
        for key, item in value.items():
            target = web_url_key(key) if isinstance(key, str) else None
            if target and target not in value and isinstance(item, str) and item.startswith('/#/'):
                result[target] = origin + item
        return result
    if isinstance(value, list):
        return [with_web_urls(item, origin) for item in value]
    return value


def normalize_origin(origin: str) -> str:
    parts = urlsplit(origin)
    if parts.scheme != 'https' or not parts.netloc or parts.path.strip('/') or parts.query or parts.fragment:
        raise ValueError('web_origin_invalid')
    return f'https://{parts.netloc}'


def attach_web_urls(server, origin: str) -> None:
    """등록된 모든 도구의 반환값에 전체 웹 URL을 덧붙인다. 도구를 모두 등록한 뒤 호출한다."""
    origin = normalize_origin(origin)
    for tool in server._tool_manager.list_tools():
        tool.fn = _wrap(tool.fn, tool.is_async, origin)


def _wrap(fn, is_async: bool, origin: str):
    if is_async:
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            return with_web_urls(await fn(*args, **kwargs), origin)
        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):  # is_async 판정 밖의 awaitable은 건드리지 않는다.
            return result
        return with_web_urls(result, origin)
    return wrapper
