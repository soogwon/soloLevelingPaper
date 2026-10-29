"""저장된 근거의 원문과 번역을 표시하는 읽기 전용 리소스."""
from importlib.resources import files

DETAIL_APP_URI = 'ui://solo-leveling/paper-detail.html'
DETAIL_APP_MIME = 'text/html;profile=mcp-app'


def register_detail_app(server):
    """호스트가 전달한 근거만 표시하며 DB나 외부 URL을 직접 읽지 않는다."""
    @server.resource(
        DETAIL_APP_URI, name='paper-detail', title='논문 근거 상세',
        mime_type=DETAIL_APP_MIME,
        meta={'ui': {'csp': {'connectDomains': [], 'resourceDomains': []}}},
    )
    def paper_detail_html() -> str:
        return files('solo_leveling.interfaces.mcp').joinpath(
            'assets/paper-detail.html',
        ).read_text(encoding='utf-8')
