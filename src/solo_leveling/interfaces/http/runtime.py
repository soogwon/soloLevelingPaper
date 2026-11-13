"""중앙 HTTP API 프로세스 진입점."""

from .settings import load_http_settings


def main() -> None:
    """환경 설정으로 Uvicorn을 실행한다."""

    import uvicorn

    settings = load_http_settings()
    uvicorn.run(
        "solo_leveling.interfaces.http.app:create_app",
        factory=True,
        access_log=False,  # 로그인 콜백의 인증 코드와 상태값을 기록하지 않는다.
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
