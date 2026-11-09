"""사용자 확인과 정확한 게시 색인 선택을 합성 MCP 세션에서 검증한다."""
import asyncio
from contextlib import closing
from unittest.mock import Mock

from mcp.shared.memory import create_connected_server_and_client_session

from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.interfaces.mcp.reparse import ReparseManager
from solo_leveling.interfaces.mcp.server import MCPServices, create_server
from tests.integration.test_reparse_service import data, no_external_api, service, count


def test_card_consent_restore_and_explicit_context_selection(data):
    worker, provider, embedder = service(data)
    manager = ReparseManager(worker)
    server = create_server(MCPServices(Mock(), Mock(), reparse=manager))
    args = dict(context_id=data.old.context_id, request_key='reparse-test')

    async def check():
        async with create_connected_server_and_client_session(server) as client:
            preview = await client.call_tool('prepare_paper_reparse', args)
            assert not preview.isError, preview.content
            token = preview.meta['confirmation_token']
            assert token not in str(preview.content)
            assert preview.structuredContent['status'] == 'AVAILABLE'
            assert not provider.calls and not count(data, 'reparse_requests')
            assert (await client.call_tool('submit_paper_reparse',
                dict(args, request_key='other', confirmation_token=token))).isError
            cancelled = await client.call_tool('cancel_paper_reparse', {'confirmation_token': token})
            assert not cancelled.isError
            assert (await client.call_tool('submit_paper_reparse', dict(args, confirmation_token=token))).isError
            preview = await client.call_tool('prepare_paper_reparse', args)
            submit = dict(args, confirmation_token=preview.meta['confirmation_token'])
            accepted = await client.call_tool('submit_paper_reparse', submit)
            assert accepted.structuredContent['status'] == 'ACCEPTED'
            assert (await client.call_tool('submit_paper_reparse', submit)).isError
            manager.begin_shutdown()
            assert await asyncio.to_thread(manager.wait_for_idle, 10)
            assert count(data, 'reparse_requests') == 1 and len(provider.calls) == 2
        fresh_worker, _, _ = service(data)
        restarted = ReparseManager(fresh_worker)
        try:
            new_server = create_server(MCPServices(Mock(), Mock(), reparse=restarted))
            async with create_connected_server_and_client_session(new_server) as client:
                status = (await client.call_tool('get_paper_reparse', args)).structuredContent
                assert status['status'] == 'COMPLETED'
                assert count(data, 'learning_contexts') == 1
                # 현재 기본 색인이 바뀌어도 선택은 완료한 작업의 게시 색인으로 고정한다.
                with closing(repo.get_connection(data.db)) as conn, conn:
                    conn.execute('UPDATE search_indexes SET is_default=0 WHERE version_id=?', ('v1',))
                    conn.execute('UPDATE search_indexes SET is_default=1 WHERE embedding_set_id=?', ('e1',))
                selection = dict(args, job_id=status['job_id'])
                assert (await client.call_tool('select_reparsed_context', dict(selection, job_id='other'))).isError
                first = (await client.call_tool('select_reparsed_context', selection)).structuredContent
                again = (await client.call_tool('select_reparsed_context', selection)).structuredContent
                assert first == again and first['embedding_set_id'] == status['new_embedding_set_id']
                assert repo.get_learning_context(data.db, first['context_id']).embedding_set_id == first['embedding_set_id']
                assert repo.get_learning_context(data.db, data.old.context_id) == data.old
                assert count(data, 'learning_contexts') == 2
        finally:
            restarted.begin_shutdown()
    try:
        asyncio.run(check())
    finally:
        manager.begin_shutdown()
        assert manager.wait_for_idle(10)


def test_blocked_provider_cannot_issue_consent(data):
    worker, provider, _ = service(data, provider_available=False)
    manager = ReparseManager(worker)
    try:
        server = create_server(MCPServices(Mock(), Mock(), reparse=manager))
        result = asyncio.run(server.call_tool('prepare_paper_reparse', {
            'context_id': data.old.context_id, 'request_key': 'blocked'}))
        assert result.structuredContent['status'] == 'BLOCKED'
        assert 'confirmation_token' not in result.meta
        assert not provider.calls and count(data, 'reparse_requests') == 0
    finally:
        manager.begin_shutdown()
