"""상태 조회 도구와 자체 포함 HTML 리소스를 연결한다."""

from importlib.resources import files

STATUS_APP_URI = 'ui://solo-leveling/paper-status.html'
STATUS_APP_MIME = 'text/html;profile=mcp-app'


def register_status_app(server):
    """화면은 DB를 직접 읽지 않고 호스트가 전달한 도구 결과만 사용한다."""
    @server.resource(
        STATUS_APP_URI, name='paper-status', title='논문 처리 상태',
        mime_type=STATUS_APP_MIME,
        meta={'ui': {'csp': {'connectDomains': [], 'resourceDomains': []}}},
    )
    def paper_status_html() -> str:
        return files('solo_leveling.interfaces.mcp').joinpath(
            'assets/paper-status.html',
        ).read_text(encoding='utf-8')
