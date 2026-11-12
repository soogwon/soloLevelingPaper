"""HTTP 프로세스 설정을 환경 변수에서 읽는다."""

from dataclasses import dataclass
from os import environ
from typing import Mapping


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
