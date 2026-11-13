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
class DatabaseSettings:
    dsn: str

    def __post_init__(self) -> None:
        scheme = urlsplit(self.dsn).scheme
        if scheme not in ("postgresql", "postgres"):
            raise ValueError("DATABASE_URL은 PostgreSQL DSN이어야 합니다.")


@dataclass(frozen=True)
class WebAuthSettings:
    authorization_url: str
    token_url: str
    userinfo_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    app_origin: str
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


def load_database_settings(source: Mapping[str, str] | None = None) -> DatabaseSettings | None:
    values = environ if source is None else source
    dsn = values.get("DATABASE_URL", "").strip()
    return DatabaseSettings(dsn) if dsn else None


def load_web_auth_settings(source: Mapping[str, str] | None = None) -> WebAuthSettings | None:
    values = environ if source is None else source
    names = ("WEB_OIDC_AUTHORIZATION_URL", "WEB_OIDC_TOKEN_URL", "WEB_OIDC_USERINFO_URL",
             "WEB_OIDC_CLIENT_ID", "WEB_OIDC_CLIENT_SECRET", "WEB_OIDC_REDIRECT_URI", "WEB_APP_ORIGIN")
    raw = tuple(values.get(name, "").strip() for name in names)
    if not any(raw):
        return None
    if not all(raw):
        raise ValueError("웹 OIDC와 WEB_APP_ORIGIN 설정을 모두 지정해야 합니다.")
    return WebAuthSettings(*raw, values.get("WEB_OIDC_SCOPES", "openid profile email").strip())


def _validate_https_url(name: str, value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError(f"{name}은 자격정보와 fragment가 없는 https URL이어야 합니다.")
