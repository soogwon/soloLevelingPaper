"""원격 MCP 응답의 웹 경로 옆에 바로 열 수 있는 전체 URL을 넣는지 검증한다."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from solo_leveling.interfaces.http.remote_mcp import WEB_LINK_INSTRUCTIONS, create_remote_mcp
from solo_leveling.interfaces.http.settings import RemoteMcpSettings
from solo_leveling.interfaces.mcp.web_links import normalize_origin, with_web_urls

ORIGIN = 'https://solo.example.com'


def settings():
    return RemoteMcpSettings(enabled=True, product_reads_enabled=True,
        resource_url=f'{ORIGIN}/mcp', audience=f'{ORIGIN}/mcp',
        issuer='https://auth.example.com/', jwks_url='https://auth.example.com/jwks',
        allowed_hosts=('solo.example.com',), allowed_client_ids=('synthetic',))


def test_adds_full_url_next_to_every_hash_path_without_removing_it():
    value = {'status': 'ok', 'web_path': '/#/remote-learning/c1',
             'result': {'items': [{'web_path': '/#/papers/p1'}, {'web_path': None}]},
             'preview': {'review_web_path': '/#/reprocessing/c1/reparse'}}
    assert with_web_urls(value, ORIGIN) == {
        'status': 'ok', 'web_path': '/#/remote-learning/c1',
        'web_url': f'{ORIGIN}/#/remote-learning/c1',
        'result': {'items': [{'web_path': '/#/papers/p1', 'web_url': f'{ORIGIN}/#/papers/p1'},
                             {'web_path': None}]},
        'preview': {'review_web_path': '/#/reprocessing/c1/reparse',
                    'review_web_url': f'{ORIGIN}/#/reprocessing/c1/reparse'}}


def test_does_not_absolutize_foreign_values_or_overwrite_existing_url():
    value = {'web_path': 'https://evil.example/#/x', 'nested': {'web_path': '/#/a', 'web_url': 'kept'}}
    assert with_web_urls(value, ORIGIN) == value
    assert with_web_urls('text', ORIGIN) == 'text'


@pytest.mark.parametrize('origin', ['http://solo.example.com', 'https://solo.example.com/app', 'https://'])
def test_rejects_non_origin_base(origin):
    with pytest.raises(ValueError):
        normalize_origin(origin)


def test_remote_tools_return_full_url_and_instructions_say_to_show_it():
    binding = NS(context_id='096bea9f-1469-41f9-9ff8-f9e686558a60', paper_id='p', server_version_id='v',
                 publication_id='pub', publication_policy='full')

    class Queries:
        pool = None

        def owner(self):
            return 'alice'

        def open_paper(self, *args, **kwargs):
            return {'status': 'ok', 'context_id': binding.context_id,
                    'web_path': f'/#/remote-learning/{binding.context_id}'}

    bundle = create_remote_mcp(settings(), product_queries=Queries())
    assert WEB_LINK_INSTRUCTIONS in bundle.server.instructions
    result = asyncio.run(bundle.server._tool_manager.call_tool(
        'open_remote_paper', {'paper_id': 'p', 'version_id': 'v', 'context_key': 'k'}))
    assert result['web_url'] == f'{ORIGIN}/#/remote-learning/{binding.context_id}'
    upload = asyncio.run(bundle.server._tool_manager.call_tool('open_remote_pdf_registration', {}))
    assert upload['upload_url'] == f'{ORIGIN}/#/uploads'
