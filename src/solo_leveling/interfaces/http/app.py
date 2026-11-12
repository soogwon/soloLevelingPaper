"""중앙 HTTP API 애플리케이션 팩터리."""

from typing import TYPE_CHECKING

from .settings import HttpSettings, load_http_settings

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_app(settings: HttpSettings | None = None) -> "FastAPI":
    """명시적 설정 또는 환경 설정으로 독립적인 API 앱을 만든다."""

    from fastapi import FastAPI

    selected = settings or load_http_settings()

    app = FastAPI(
        title="Solo Leveling Paper API",
        version="0.1.0",
    )
    app.state.settings = selected

    @app.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "solo-leveling-api",
            "environment": selected.environment,
        }

    return app
