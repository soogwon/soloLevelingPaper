"""설치 이미지에서 제품 조회 경로를 조립한다. DB·인증 서버에 접속하지 않는다."""
import asyncio
import sys
import os
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

# 동의 웹과 MCP 쓰기 기능을 함께 켠 조립도 네트워크 없이 확인한다.
os.environ.update({
    'REMOTE_ANNOTATIONS_ENABLED': 'true',
    'REMOTE_ANNOTATION_WEB_DATABASE_URL': 'postgresql://web:synthetic@127.0.0.1/test',
    'REMOTE_ANNOTATION_MCP_DATABASE_URL': 'postgresql://mcp:synthetic@127.0.0.1/test',
    'REMOTE_ANNOTATION_SERVICE_ID': 'image-verification',
})
annotation_app = create_app(remote_mcp_settings=settings)
assert any(getattr(route, 'path', '') == '/v1/remote-annotation-products/contexts/{context}/consent'
           for route in annotation_app.routes)
from solo_leveling.interfaces.http.remote_annotation_products import RemoteAnnotationProducts
annotation_bundle = create_remote_mcp(settings, product_queries=Queries(),
    annotation_service=RemoteAnnotationProducts(None, 'image-verification'))
annotation_names = {tool.name for tool in asyncio.run(annotation_bundle.server.list_tools())}
assert {'start_remote_learning', 'create_remote_annotation', 'list_remote_annotations',
        'update_remote_annotation', 'delete_remote_annotation',
        'recover_remote_annotation_request', 'confirm_remote_annotation_request'} <= annotation_names
assert not any('consent' in name for name in annotation_names)
assert {'annotations:read', 'annotations:write'} <= set(annotation_bundle.server.settings.auth.required_scopes)
print('REMOTE_ANNOTATION_IMAGE_OK; no database or provider calls')
