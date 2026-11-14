"""논문 서버 동기화는 카드의 명시적 승인 뒤에만 실행된다."""

import asyncio
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from solo_leveling.application.server_sync import SyncResult
from solo_leveling.application.server_sync.paper_metadata import PaperSyncPreview
from solo_leveling.interfaces.mcp.server import MCPServices, create_server
from solo_leveling.interfaces.mcp.server_sync_tools import SERVER_SYNC_APP_URI, SERVER_SYNC_NOTICE_VERSION


def make_server(service):
    return create_server(MCPServices(Mock(), Mock(), server_sync=service))


def test_card_approval_creates_and_sends_exactly_one_operation():
    service = Mock()
    service.preview.return_value = PaperSyncPreview("account", "paper-1", "Paper", "created")
    service.grant_and_queue.return_value = SyncResult("op-1", "pending")
    service.sync.return_value = SyncResult("op-1", "synced", "server-paper-1")
    server = make_server(service)

    async def check():
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert tools["prepare_paper_server_sync"].meta["ui"]["resourceUri"] == SERVER_SYNC_APP_URI
        for name in ("sync_paper_to_server", "cancel_paper_server_sync"):
            assert tools[name].meta["ui"]["visibility"] == ["app"]
        prepared = await server.call_tool("prepare_paper_server_sync", {"paper_id": "paper-1"})
        service.grant_and_queue.assert_not_called()
        assert prepared.structuredContent["confirmation_token"] == prepared.meta["confirmation_token"]
        assert prepared.structuredContent["title"] == "Paper"
        operation_id = prepared.structuredContent["operation_id"]
        assert all(term in prepared.structuredContent["notice"] for term in
                   ("제목", "학습 위치", "완료한 학습 단계", "요약 본문", "근거 ID",
                    "PDF 파일", "추출 원문", "근거 본문", "메모", "연결된 계정"))
        assert prepared.structuredContent["learning_record_count"] == 0
        assert prepared.structuredContent["summary_count"] == 0
        assert "학습 기록 0건 · 저장된 전체 논문 요약 0건" in prepared.structuredContent["notice"]
        token = prepared.meta["confirmation_token"]
        result = await server.call_tool("sync_paper_to_server", {
            "paper_id": "paper-1", "operation_id": operation_id, "confirmation_token": token})
        assert result[1] == {"operation_id": "op-1", "status": "synced",
                             "server_object_id": "server-paper-1", "error_code": None}
        with pytest.raises(ToolError):
            await server.call_tool("sync_paper_to_server", {
                "paper_id": "paper-1", "operation_id": operation_id, "confirmation_token": token})

    asyncio.run(check())
    service.preview.assert_called_once_with("paper-1")
    queued_args = service.grant_and_queue.call_args.args
    assert queued_args[:2] == ("paper-1", SERVER_SYNC_NOTICE_VERSION)
    assert queued_args[3] == service.preview.return_value
    assert len(queued_args[2]) == 36
    service.sync.assert_called_once_with("op-1")


def test_token_is_bound_to_paper_and_cancel_does_not_queue():
    service = Mock()
    service.preview.side_effect = lambda paper_id: PaperSyncPreview("account", paper_id, None, "created")
    server = make_server(service)

    async def check():
        prepared = await server.call_tool("prepare_paper_server_sync", {"paper_id": "paper-1"})
        token = prepared.meta["confirmation_token"]
        operation_id = prepared.structuredContent["operation_id"]
        with pytest.raises(ToolError):
            await server.call_tool("sync_paper_to_server", {
                "paper_id": "paper-2", "operation_id": operation_id, "confirmation_token": token})
        await server.call_tool("cancel_paper_server_sync", {"confirmation_token": token})
        with pytest.raises(ToolError):
            await server.call_tool("sync_paper_to_server", {
                "paper_id": "paper-1", "operation_id": operation_id, "confirmation_token": token})

    asyncio.run(check())
    service.grant_and_queue.assert_not_called()


def test_status_is_read_only_and_does_not_retry():
    service = Mock()
    service.status.return_value = SyncResult("op-1", "retryable_failure", error_code="server_unavailable")
    server = make_server(service)

    result = asyncio.run(server.call_tool("get_paper_server_sync", {"operation_id": "op-1"}))

    assert result[1]["status"] == "retryable_failure"
    assert result[1]["error_code"] == "server_unavailable"
    service.status.assert_called_once_with("op-1")
    service.sync.assert_not_called()


def test_resource_is_bundled_without_external_dependencies():
    server = make_server(Mock())

    async def check():
        resources = await server.list_resources()
        resource = next(item for item in resources if str(item.uri) == SERVER_SYNC_APP_URI)
        assert resource.mimeType == "text/html;profile=mcp-app"
        content = await server.read_resource(SERVER_SYNC_APP_URI)
        html = content[0].content
        assert "동의하고 동기화" in html
        assert '<script src=' not in html

    asyncio.run(check())


@pytest.mark.parametrize('state', ['pending', 'retryable_failure'])
def test_retry_reuses_operation_without_new_consent(state):
    service = Mock()
    service.status.return_value = SyncResult('existing', state)
    service.sync.return_value = SyncResult('existing', 'synced', 'server-paper')
    server = make_server(service)
    result = asyncio.run(server.call_tool('retry_paper_server_sync', {'operation_id': 'existing'}))
    assert result[1]['status'] == 'synced'
    service.sync.assert_called_once_with('existing')
    service.grant_and_queue.assert_not_called()
    service.preview.assert_not_called()


@pytest.mark.parametrize('state', ['synced', 'conflict', 'action_required', 'syncing'])
def test_retry_does_not_send_blocked_or_completed_operation(state):
    service = Mock()
    service.status.return_value = SyncResult('existing', state)
    result = asyncio.run(make_server(service).call_tool(
        'retry_paper_server_sync', {'operation_id': 'existing'}))
    assert result[1]['status'] == state
    service.sync.assert_not_called()


def test_retry_unknown_or_other_accounts_operation_does_not_send():
    service = Mock()
    service.status.side_effect = KeyError('private')
    with pytest.raises(ToolError, match='NOT_FOUND'):
        asyncio.run(make_server(service).call_tool(
            'retry_paper_server_sync', {'operation_id': 'unknown'}))
    service.sync.assert_not_called()


@pytest.mark.parametrize('change, expected', [('none', 'synced'), ('payload', 'conflict'), ('revoked', 'action_required')])
def test_retry_with_real_outbox_preserves_consent_and_idempotency(tmp_path, change, expected):
    from solo_leveling.application.server_sync import PaperMetadataSyncService
    from solo_leveling.domain.models import Paper
    from solo_leveling.infrastructure.database.repository import insert_paper
    from solo_leveling.infrastructure.database.schema import get_connection, init_db
    from tests.unit.test_paper_metadata_sync import Client, Response

    db = str(tmp_path / 'sync.sqlite')
    init_db(db)
    insert_paper(db, Paper('paper-1', 'Paper', created_at='2026-11-01T10:00:00+09:00'))
    client = Client([ConnectionError('offline'), Response()])
    service = PaperMetadataSyncService(db, client)
    operation = service.grant_and_queue('paper-1', SERVER_SYNC_NOTICE_VERSION).operation_id
    assert service.sync(operation).status == 'retryable_failure'
    with get_connection(db) as conn:
        if change == 'payload':
            conn.execute("UPDATE papers SET title='Changed' WHERE paper_id='paper-1'")
        elif change == 'revoked':
            conn.execute("UPDATE server_sync_consents SET revoked_at='2026-11-14T00:00:00Z'")
    restarted = make_server(PaperMetadataSyncService(db, client))
    result = asyncio.run(restarted.call_tool('retry_paper_server_sync', {'operation_id': operation}))
    assert result[1]['status'] == expected
    assert len(client.calls) == (2 if change == 'none' else 1)
    assert all(call[2]['headers']['Idempotency-Key'] == operation for call in client.calls)
    with get_connection(db) as conn:
        assert conn.execute('SELECT count(*) FROM server_sync_consents').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM sync_outbox').fetchone()[0] == 1


def test_concurrent_retry_runs_once_and_can_retry_after_error():
    from threading import Event
    import anyio

    started, release = Event(), Event()
    service = Mock()
    service.status.return_value = SyncResult('existing', 'retryable_failure')

    def run(operation_id):
        started.set()
        if not release.wait(5):
            raise RuntimeError('test timeout')
        raise RuntimeError('private failure')

    service.sync.side_effect = run
    server = make_server(service)

    async def scenario():
        with anyio.fail_after(10):
            async def first():
                with pytest.raises(ToolError, match='INTERNAL_ERROR'):
                    await server.call_tool('retry_paper_server_sync', {'operation_id': 'existing'})
            async with anyio.create_task_group() as group:
                group.start_soon(first)
                while not started.is_set():
                    await anyio.sleep(0.001)
                try:
                    with pytest.raises(ToolError, match='SERVER_SYNC_BUSY'):
                        await server.call_tool('retry_paper_server_sync', {'operation_id': 'existing'})
                finally:
                    release.set()
            assert service.sync.call_count == 1
            service.sync.side_effect = None
            service.sync.return_value = SyncResult('existing', 'synced')
            result = await server.call_tool('retry_paper_server_sync', {'operation_id': 'existing'})
            assert result[1]['status'] == 'synced'
            assert service.sync.call_count == 2

    try:
        asyncio.run(scenario())
    finally:
        release.set()
