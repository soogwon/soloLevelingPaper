"""설치 이미지에서 제품 조회 경로를 조립한다. DB·인증 서버에 접속하지 않는다."""
import asyncio
import sys
from solo_leveling.interfaces.http.app import create_app
from solo_leveling.interfaces.http.settings import RemoteMcpSettings

settings = RemoteMcpSettings(
    enabled=True, product_reads_enabled=True,
    resource_url='https://mcp.example.com/mcp', audience='https://mcp.example.com/mcp',
    issuer='https://auth.example.com/', jwks_url='https://auth.example.com/jwks',
    allowed_hosts=('mcp.example.com',), allowed_client_ids=('synthetic-client',),
)
# 진짜 조회 객체의 import와 전용 풀 조립까지 검사하며 lifespan은 시작하지 않는다.
app = create_app(remote_mcp_settings=settings)
assert app is not None
assert 'solo_leveling.interfaces.mcp.server' not in sys.modules
from solo_leveling.interfaces.http.remote_mcp import create_remote_mcp
class Queries:
    pass
bundle = create_remote_mcp(settings, product_queries=Queries())
names = {tool.name for tool in asyncio.run(bundle.server.list_tools())}
assert {'list_remote_papers', 'open_remote_paper', 'get_remote_question'} <= names
assert 'submit_paper_question' not in names
print('REMOTE_PRODUCT_IMAGE_OK; no database or provider calls')
