"""HTTP 프로세스 설정을 환경 변수에서 읽는다."""

from dataclasses import dataclass
from os import environ
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class HttpSettings:
    """중앙 API 프로세스의 최소 실행 설정."""

    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000

    def __post_init__(self) -> None:
        if not self.environment.strip():
            raise ValueError("APP_ENV는 비어 있을 수 없습니다.")
        if not self.host.strip():
            raise ValueError("API_HOST는 비어 있을 수 없습니다.")
        if not 1 <= self.port <= 65535:
            raise ValueError("API_PORT는 1에서 65535 사이여야 합니다.")


@dataclass(frozen=True)
class OidcSettings:
    """신뢰할 OIDC issuer와 RS256 공개 키 집합 설정."""

    issuer: str
    audience: str
    jwks_url: str

    def __post_init__(self) -> None:
        if not self.audience.strip():
            raise ValueError("OIDC_AUDIENCE는 비어 있을 수 없습니다.")
        _validate_https_url("OIDC_ISSUER", self.issuer)
        _validate_https_url("OIDC_JWKS_URL", self.jwks_url)


@dataclass(frozen=True)
class RemoteMcpSettings:
    """공개 원격 MCP 스파이크의 독립 Auth0·전송 경계."""

    enabled: bool = False
    resource_url: str = ""
    issuer: str = ""
    audience: str = ""
    jwks_url: str = ""
    required_scope: str = "spike:read"
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    clock_skew_seconds: int = 60

    def __post_init__(self) -> None:
        if not self.enabled:
            return
        for name, value in (("REMOTE_MCP_RESOURCE_URL", self.resource_url),
                            ("REMOTE_MCP_OIDC_ISSUER", self.issuer),
                            ("REMOTE_MCP_OIDC_AUDIENCE", self.audience),
                            ("REMOTE_MCP_OIDC_JWKS_URL", self.jwks_url)):
            if not value.strip():
                raise ValueError(f"{name}은 비어 있을 수 없습니다.")
        _validate_https_url("REMOTE_MCP_RESOURCE_URL", self.resource_url)
        _validate_https_url("REMOTE_MCP_OIDC_ISSUER", self.issuer)
        _validate_https_url("REMOTE_MCP_OIDC_JWKS_URL", self.jwks_url)
        resource = urlsplit(self.resource_url)
        if resource.path != "/mcp" or resource.query:
            raise ValueError("REMOTE_MCP_RESOURCE_URL 경로는 쿼리 없는 /mcp여야 합니다.")
        if not self.required_scope.strip() or any(char.isspace() for char in self.required_scope):
            raise ValueError("REMOTE_MCP_REQUIRED_SCOPE는 공백 없는 단일 scope여야 합니다.")
        if not 0 <= self.clock_skew_seconds <= 300:
            raise ValueError("REMOTE_MCP_CLOCK_SKEW_SECONDS는 0에서 300 사이여야 합니다.")
        if self.audience != self.resource_url:
            raise ValueError("원격 MCP 전용 audience는 resource URL과 같아야 합니다.")
        public_host = resource.netloc
        if not self.allowed_hosts or public_host not in self.allowed_hosts:
            raise ValueError("REMOTE_MCP_ALLOWED_HOSTS에는 공개 resource hostname이 필요합니다.")
        for host in self.allowed_hosts:
            if not host or "://" in host or "/" in host:
                raise ValueError("REMOTE_MCP_ALLOWED_HOSTS에는 hostname 또는 hostname:port만 지정합니다.")
        for origin in self.allowed_origins:
            _validate_origin("REMOTE_MCP_ALLOWED_ORIGINS", origin)


@dataclass(frozen=True)
class DatabaseSettings:
    dsn: str

    def __post_init__(self) -> None:
        scheme = urlsplit(self.dsn).scheme
        if scheme not in ("postgresql", "postgres"):
            raise ValueError("DATABASE_URL은 PostgreSQL DSN이어야 합니다.")


@dataclass(frozen=True)
class ObjectStorageSettings:
    endpoint_url: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    region: str = "auto"

    def __post_init__(self) -> None:
        _validate_https_url("OBJECT_STORAGE_ENDPOINT", self.endpoint_url)
        if any(not value.strip() for value in (
                self.bucket, self.access_key_id, self.secret_access_key, self.region)):
            raise ValueError("객체 저장소 설정은 비어 있을 수 없습니다.")


@dataclass(frozen=True)
class WebAuthSettings:
    authorization_url: str
    token_url: str
    userinfo_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    app_origin: str
    audience: str
    scopes: str = "openid profile email"

    def __post_init__(self) -> None:
        for name, value in (("WEB_OIDC_AUTHORIZATION_URL", self.authorization_url),
                            ("WEB_OIDC_TOKEN_URL", self.token_url),
                            ("WEB_OIDC_USERINFO_URL", self.userinfo_url),
                            ("WEB_OIDC_REDIRECT_URI", self.redirect_uri),
                            ("WEB_APP_ORIGIN", self.app_origin)):
            _validate_https_url(name, value)
        if not self.client_id.strip() or not self.client_secret.strip():
            raise ValueError("웹 OIDC client 설정이 비어 있습니다.")
        if not self.audience.strip():
            raise ValueError("웹 OIDC API audience가 비어 있습니다.")
        if "openid" not in self.scopes.split():
            raise ValueError("WEB_OIDC_SCOPES에는 openid가 필요합니다.")
        redirect = urlsplit(self.redirect_uri)
        if redirect.query or redirect.path != "/v1/auth/callback":
            raise ValueError("WEB_OIDC_REDIRECT_URI는 /v1/auth/callback 이어야 합니다.")
        origin = urlsplit(self.app_origin)
        if origin.path not in ("", "/") or origin.query:
            raise ValueError("WEB_APP_ORIGIN에는 origin만 지정해야 합니다.")
        if (redirect.scheme, redirect.netloc) != (origin.scheme, origin.netloc):
            raise ValueError("웹 callback은 WEB_APP_ORIGIN과 같은 origin이어야 합니다.")


def load_http_settings(source: Mapping[str, str] | None = None) -> HttpSettings:
    """프로세스 환경 또는 전달된 매핑에서 HTTP 설정을 구성한다."""

    values = environ if source is None else source
    raw_port = values.get("API_PORT") or values.get("PORT", "8000")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("API_PORT는 정수여야 합니다.") from exc
    return HttpSettings(
        environment=values.get("APP_ENV", "development"),
        host=values.get("API_HOST", "127.0.0.1"),
        port=port,
    )


def load_web_static_root(source: Mapping[str, str] | None = None) -> Path | None:
    """같은 origin에서 제공할 외부 사이트 디렉터리를 읽고 검증한다."""

    values = environ if source is None else source
    raw = values.get("WEB_STATIC_ROOT", "").strip()
    if not raw:
        return None
    root = Path(raw)
    if not root.is_absolute():
        raise ValueError("WEB_STATIC_ROOT는 절대 경로여야 합니다.")
    if not root.is_dir():
        raise ValueError("WEB_STATIC_ROOT 디렉터리를 찾을 수 없습니다.")
    if not (root / "index.html").is_file():
        raise ValueError("WEB_STATIC_ROOT에 index.html이 필요합니다.")
    return root


def load_oidc_settings(source: Mapping[str, str] | None = None) -> OidcSettings | None:
    """세 값이 모두 없으면 미설정, 일부만 있으면 잘못된 설정으로 처리한다."""

    values = environ if source is None else source
    raw = tuple(values.get(name, "").strip() for name in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL"))
    if not any(raw):
        return None
    if not all(raw):
        raise ValueError("OIDC_ISSUER, OIDC_AUDIENCE, OIDC_JWKS_URL을 모두 설정해야 합니다.")
    return OidcSettings(*raw)


def load_remote_mcp_settings(source: Mapping[str, str] | None = None) -> RemoteMcpSettings:
    values = environ if source is None else source
    enabled = _parse_bool("REMOTE_MCP_SPIKE_ENABLED", values.get("REMOTE_MCP_SPIKE_ENABLED", "false"))
    if not enabled:
        return RemoteMcpSettings()
    resource_url = values.get("REMOTE_MCP_RESOURCE_URL", "").strip()
    public_host = urlsplit(resource_url).netloc if resource_url else ""
    configured_hosts = _split_csv(values.get("REMOTE_MCP_ALLOWED_HOSTS", ""))
    allowed_hosts = configured_hosts or ((public_host,) if public_host else ())
    allowed_origins = _split_csv(values.get("REMOTE_MCP_ALLOWED_ORIGINS", "https://claude.ai"))
    raw_skew = values.get("REMOTE_MCP_CLOCK_SKEW_SECONDS", "60").strip()
    try:
        clock_skew_seconds = int(raw_skew)
    except ValueError as exc:
        raise ValueError("REMOTE_MCP_CLOCK_SKEW_SECONDS는 정수여야 합니다.") from exc
    return RemoteMcpSettings(
        enabled=True,
        resource_url=resource_url,
        issuer=values.get("REMOTE_MCP_OIDC_ISSUER", "").strip(),
        audience=values.get("REMOTE_MCP_OIDC_AUDIENCE", "").strip(),
        jwks_url=values.get("REMOTE_MCP_OIDC_JWKS_URL", "").strip(),
        required_scope=values.get("REMOTE_MCP_REQUIRED_SCOPE", "spike:read").strip(),
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
        clock_skew_seconds=clock_skew_seconds,
    )


def load_database_settings(source: Mapping[str, str] | None = None) -> DatabaseSettings | None:
    values = environ if source is None else source
    dsn = values.get("DATABASE_URL", "").strip()
    return DatabaseSettings(dsn) if dsn else None


def load_object_storage_settings(
    source: Mapping[str, str] | None = None,
) -> ObjectStorageSettings | None:
    values = environ if source is None else source
    names = ("OBJECT_STORAGE_ENDPOINT", "OBJECT_STORAGE_BUCKET",
             "OBJECT_STORAGE_ACCESS_KEY_ID", "OBJECT_STORAGE_SECRET_ACCESS_KEY")
    raw = tuple(values.get(name, "").strip() for name in names)
    if not any(raw):
        return None
    if not all(raw):
        raise ValueError("객체 저장소 설정을 모두 지정해야 합니다.")
    return ObjectStorageSettings(*raw, values.get("OBJECT_STORAGE_REGION", "auto").strip())


def load_web_auth_settings(source: Mapping[str, str] | None = None) -> WebAuthSettings | None:
    values = environ if source is None else source
    names = ("WEB_OIDC_AUTHORIZATION_URL", "WEB_OIDC_TOKEN_URL", "WEB_OIDC_USERINFO_URL",
             "WEB_OIDC_CLIENT_ID", "WEB_OIDC_CLIENT_SECRET", "WEB_OIDC_REDIRECT_URI", "WEB_APP_ORIGIN")
    raw = tuple(values.get(name, "").strip() for name in names)
    if not any(raw):
        return None
    if not all(raw):
        raise ValueError("웹 OIDC와 WEB_APP_ORIGIN 설정을 모두 지정해야 합니다.")
    audience = values.get("OIDC_AUDIENCE", "").strip()
    if not audience:
        raise ValueError("웹 OIDC에는 OIDC_AUDIENCE가 필요합니다.")
    return WebAuthSettings(
        *raw,
        audience,
        values.get("WEB_OIDC_SCOPES", "openid profile email").strip(),
    )


def _validate_https_url(name: str, value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError(f"{name}은 자격정보와 fragment가 없는 https URL이어야 합니다.")


def _validate_origin(name: str, value: str) -> None:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError(f"{name}에는 자격정보·경로·쿼리 없는 https origin만 지정합니다.")


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off", ""):
        return False
    raise ValueError(f"{name}은 true 또는 false여야 합니다.")
