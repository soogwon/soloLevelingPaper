"""웹 동의 권한과 MCP 쓰기 권한을 분리한 명시적 제품 설정."""
from dataclasses import dataclass, field
import os
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class ReflectionSettings:
    web_dsn: str = field(repr=False)
    mcp_dsn: str = field(repr=False)


def load_reflection_settings(env=None):
    env = os.environ if env is None else env
    enabled = env.get('REMOTE_REFLECTIONS_ENABLED', 'false')
    if enabled == 'false':
        return None
    try:
        if enabled != 'true':
            raise ValueError()
        web = env['REMOTE_REFLECTION_WEB_DATABASE_URL'].strip()
        mcp = env['REMOTE_REFLECTION_MCP_DATABASE_URL'].strip()
        urls = [urlsplit(dsn) for dsn in (web, mcp)]
        for url in urls:
            if (url.scheme not in ('postgres', 'postgresql') or not url.hostname
                    or not url.username or not url.password or url.path in ('', '/') or url.fragment):
                raise ValueError()
        a, b = urls
        # 같은 DB·검색 경로의 서로 다른 역할만 조립한다. 실제 GRANT 검증은 배포 전에 별도로 한다.
        if (unquote(a.username) == unquote(b.username)
                or (a.hostname, a.port or 5432, a.path, a.query) != (b.hostname, b.port or 5432, b.path, b.query)):
            raise ValueError()
        return ReflectionSettings(web, mcp)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError('reflection_settings_invalid') from None
