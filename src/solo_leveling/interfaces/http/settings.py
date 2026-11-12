"""HTTP 프로세스 설정을 환경 변수에서 읽는다."""

from dataclasses import dataclass
from os import environ
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


def load_http_settings(source: Mapping[str, str] | None = None) -> HttpSettings:
    """프로세스 환경 또는 전달된 매핑에서 HTTP 설정을 구성한다."""

    values = environ if source is None else source
    raw_port = values.get("API_PORT", "8000")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("API_PORT는 정수여야 합니다.") from exc
    return HttpSettings(
        environment=values.get("APP_ENV", "development"),
        host=values.get("API_HOST", "127.0.0.1"),
        port=port,
    )


def load_oidc_settings(source: Mapping[str, str] | None = None) -> OidcSettings | None:
    """세 값이 모두 없으면 미설정, 일부만 있으면 잘못된 설정으로 처리한다."""

    values = environ if source is None else source
    raw = tuple(values.get(name, "").strip() for name in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL"))
    if not any(raw):
        return None
    if not all(raw):
        raise ValueError("OIDC_ISSUER, OIDC_AUDIENCE, OIDC_JWKS_URL을 모두 설정해야 합니다.")
    return OidcSettings(*raw)


def _validate_https_url(name: str, value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError(f"{name}은 자격정보와 fragment가 없는 https URL이어야 합니다.")
