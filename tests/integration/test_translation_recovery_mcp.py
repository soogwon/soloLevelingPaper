"""실제 API 없이 동의 토큰·MCP 왕복·복구 상태·종료 수명을 검증한다."""
import asyncio
import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session

from solo_leveling.interfaces.cli.safe_diagnostic_stream import SafeDiagnosticStream
from solo_leveling.interfaces.mcp.ingestion import IngestionShuttingDownError
from solo_leveling.interfaces.mcp.lifecycle import RequestGate, RuntimeLifetime
from solo_leveling.interfaces.mcp.recovery import RecoveryManager, RECOVERY_URI
from solo_leveling.interfaces.mcp.server import MCPServices, create_server
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.retrieval.sqlite_chroma import SQLiteContextReader
from tests.integration.test_translation_recovery_service import db, setup as service_setup
from tests.unit.test_translation_recovery import add_second_partial_version


ARGS = dict(version_id='v1', base_embedding_set_id='e1', request_key='SYNTHETIC_REQUEST_KEY')


@pytest.fixture
def connected(service_setup, db):
    service, provider, embeddings, client = service_setup
    manager = RecoveryManager(service)
    server = create_server(MCPServices(Mock(), Mock(), contexts=SQLiteContextReader(db), recovery=manager))
    try:
        yield server, manager, provider, embeddings, client
    finally:
        manager.begin_shutdown()
        assert manager.wait_for_idle(15)


def test_missing_recovery_has_public_not_found_error(connected):
    server, *_ = connected

    async def check():
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool('get_translation_recovery', ARGS)
            assert result.isError and result.structuredContent is None
            assert [part.text for part in result.content] == ['NOT_FOUND: 요청한 자료를 찾을 수 없습니다.']

    asyncio.run(check())


def test_preview_cancellation_and_forged_or_swapped_tokens_do_not_execute(db, connected):
    server, manager, provider, embeddings, _ = connected
    async def check():
        tools = {t.name: t for t in await server.list_tools()}
        assert tools['recover_translation'].meta['ui']['visibility'] == ['app']
        assert tools['prepare_translation_recovery'].meta['ui']['resourceUri'] == RECOVERY_URI
        assert tools['get_translation_recovery'].annotations.readOnlyHint
        prepared = await server.call_tool('prepare_translation_recovery', ARGS)
        token = prepared.meta['confirmation_token']
        assert all(token not in part.text for part in prepared.content)
        assert json.loads(prepared.content[0].text) == prepared.structuredContent
        assert '카드 표시 여부는 이 도구 응답으로 확인할 수 없습니다' in prepared.content[1].text
        assert 'confirmation_token' not in prepared.structuredContent
        assert prepared.structuredContent['target_count'] == 2
        for changed in ({'confirmation_token': 'forged'}, {'base_embedding_set_id': 'other'},
                        {'version_id': 'other'}, {'request_key': 'other'}):
            with pytest.raises(ToolError):
                await server.call_tool('recover_translation', {**ARGS, 'confirmation_token': token, **changed})
        await server.call_tool('cancel_translation_recovery', {'confirmation_token': token})
        with pytest.raises(ToolError):
            await server.call_tool('recover_translation', {**ARGS, 'confirmation_token': token})
    asyncio.run(check())
    with repo.get_connection(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM translation_recovery_requests').fetchone()[0] == 0
    assert not provider.calls and not embeddings


@pytest.mark.parametrize('failures,outcome,count', [(set(), 'FULL', 2), ({'c7'}, 'PARTIAL', 1),
                                                   ({'c3', 'c7'}, 'NO_PROGRESS', 0)])
def test_protocol_consent_submit_lookup_preserves_context(db, connected, failures, outcome, count):
    server, manager, provider, embeddings, client = connected
    provider.failures = failures
    old = repo.get_or_create_default_context(db, 'v1')
    async def check():
        async with create_connected_server_and_client_session(server) as session:
            preview = await session.call_tool('prepare_translation_recovery', ARGS)
            assert not provider.calls
            token = preview.meta['confirmation_token']
            accepted = await session.call_tool('recover_translation', {**ARGS, 'confirmation_token': token})
            assert not accepted.isError and accepted.structuredContent['status'] == 'ACCEPTED'
            assert await asyncio.to_thread(manager.wait_for_idle, 20)
            result = await session.call_tool('get_translation_recovery', ARGS)
            assert not result.isError
            assert result.structuredContent['outcome'] == outcome
            assert result.structuredContent['recovered_count'] == count
            assert result.structuredContent['status'] == ('FAILED' if count == 0 else 'COMPLETED')
            again = await session.call_tool('get_translation_recovery', ARGS)
            assert again.structuredContent == result.structuredContent
            selection = dict(selection_key='selection-1', request_key=ARGS['request_key'],
                             version_id='v1', job_id=result.structuredContent['job_id'],
                             new_embedding_set_id=result.structuredContent['new_embedding_set_id'] or 'missing')
            selected = await session.call_tool('select_recovery_context', selection)
            if count:
                assert not selected.isError
                data = selected.structuredContent
                assert data['context_id'] != old.context_id
                assert data['embedding_set_id'] == selection['new_embedding_set_id']
                replay = await session.call_tool('select_recovery_context', selection)
                assert replay.structuredContent == data
                conflict = await session.call_tool('select_recovery_context', {**selection, 'goal': 'skim'})
                assert conflict.isError
            else:
                assert selected.isError
            duplicate = await session.call_tool('recover_translation', {**ARGS, 'confirmation_token': token})
            assert duplicate.isError
    asyncio.run(check())
    assert provider.calls == ['c3', 'c7']
    saved = repo.get_learning_context(db, old.context_id)
    assert saved == old


def test_submission_response_loss_then_fresh_preview_replays_same_job(db, connected, monkeypatch):
    server, manager, provider, embeddings, _ = connected
    original = manager.submit
    def lost(*args):
        original(*args)
        raise OSError('SYNTHETIC_PRIVATE_ERROR')
    monkeypatch.setattr(manager, 'submit', lost)
    async def check():
        preview = await server.call_tool('prepare_translation_recovery', ARGS)
        with pytest.raises(ToolError) as error:
            await server.call_tool('recover_translation', {**ARGS, 'confirmation_token': preview.meta['confirmation_token']})
        assert 'SYNTHETIC_PRIVATE_ERROR' not in str(error.value)
        assert await asyncio.to_thread(manager.wait_for_idle, 20)
        result = (await server.call_tool('get_translation_recovery', ARGS))[1]
        assert result['status'] == 'COMPLETED'
        # 읽기뿐인 새 안내는 추가 작업을 만들지 않는다.
        await server.call_tool('prepare_translation_recovery', ARGS)
        assert manager.lookup(**ARGS)['job_id'] == result['job_id']
    asyncio.run(check())
    with repo.get_connection(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM translation_recovery_requests').fetchone()[0] == 1
    assert len(provider.calls) == 2 and len(embeddings) == 1


def test_executor_submission_failure_is_replayed_not_executed(db, connected, monkeypatch):
    _, manager, provider, embeddings, _ = connected
    def reject(*a, **kw):
        raise RuntimeError('SYNTHETIC_PRIVATE_ERROR')
    monkeypatch.setattr(manager.executor, 'submit', reject)
    result = manager.submit(**ARGS)
    assert result['status'] == 'FAILED' and result['reason_code'] == 'RECOVERY_SUBMISSION_FAILED'
    assert manager.submit(**ARGS) == result
    assert not provider.calls and not embeddings


def test_runtime_does_not_close_shared_resources_before_recovery_finishes(connected):
    _, manager, provider, _, _ = connected
    entered, release = threading.Event(), threading.Event()
    def pause():
        entered.set()
        assert release.wait(8)
    provider.hook = pause
    manager.submit(**ARGS)
    assert entered.wait(5)
    order = []
    embedder = SimpleNamespace(close=lambda: order.append('embedder'))
    client = SimpleNamespace(close=lambda: order.append('client'))
    lifetime = RuntimeLifetime(RequestGate(), Mock(wait_for_idle=Mock(return_value=True)),
                               embedder, client, additional_workers=(manager,))
    lifetime.begin()
    try:
        assert not lifetime.done.wait(.05)
        assert not order
        with pytest.raises(IngestionShuttingDownError):
            manager.submit(**ARGS)
    finally:
        release.set()
        assert lifetime.done.wait(20)
    assert order == ['embedder', 'client']


def test_recovery_diagnostic_filter_allows_only_fixed_safe_codes():
    output = io.StringIO()
    stream = SafeDiagnosticStream(output)
    for value in ({'event': 'translation_recovery', 'code': 'RECOVERY_INDEX_FAILED'},
                  {'event': 'translation_recovery', 'code': 'SYNTHETIC_PRIVATE_ERROR'},
                  {'event': 'translation_recovery', 'code': 'RECOVERY_INDEX_FAILED', 'text': 'SYNTHETIC_PRIVATE_ERROR'},
                  {'event': 'translation_recovery', 'code': []}):
        stream.write(json.dumps(value) + '\n')
    assert [json.loads(row) for row in output.getvalue().splitlines()] == [
        {'event': 'translation_recovery', 'code': 'RECOVERY_INDEX_FAILED'}]


def test_preview_without_base_returns_exact_id_and_unavailable_has_no_token(connected):
    server, manager, provider, embeddings, _ = connected
    async def check():
        preview = await server.call_tool('prepare_translation_recovery',
            {'version_id': 'v1', 'request_key': 'key'})
        assert preview.structuredContent['base_embedding_set_id'] == 'e1'
        manager.service.provider_available = False
        disabled = await server.call_tool('prepare_translation_recovery', ARGS)
        assert disabled.structuredContent['status'] == 'UNAVAILABLE'
        assert not disabled.meta or 'confirmation_token' not in disabled.meta
    asyncio.run(check())
    assert not provider.calls and not embeddings


def test_shutdown_cancels_queued_recovery_without_provider_calls(db, connected):
    _, manager, provider, embeddings, _ = connected
    add_second_partial_version(db)
    entered, release = threading.Event(), threading.Event()
    def pause():
        entered.set()
        assert release.wait(8)
    provider.hook = pause
    manager.submit(**ARGS)
    assert entered.wait(5)
    second = dict(version_id='v2', base_embedding_set_id='e10', request_key='second')
    try:
        queued = manager.submit(**second)
        assert queued['status'] == 'ACCEPTED'
        manager.begin_shutdown()
        cancelled = manager.lookup(**second)
        assert cancelled['status'] == 'FAILED'
        assert cancelled['reason_code'] == 'RECOVERY_SUBMISSION_FAILED'
    finally:
        release.set()
        assert manager.wait_for_idle(20)
    assert provider.calls == ['c3', 'c7']
    assert len(embeddings) == 1


@pytest.mark.parametrize('failure', ['begin', 'drain'])
def test_recovery_drain_failure_preserves_shared_resources(failure, capsys):
    worker = Mock(wait_for_idle=Mock(return_value=False))
    if failure == 'begin':
        worker.begin_shutdown.side_effect = RuntimeError('SYNTHETIC_PRIVATE_ERROR')
    embedder, client = Mock(), Mock()
    lifetime = RuntimeLifetime(RequestGate(), Mock(wait_for_idle=Mock(return_value=True)),
                               embedder, client, additional_workers=(worker,))
    lifetime.begin()
    assert lifetime.done.wait(5)
    embedder.close.assert_not_called()
    client.close.assert_not_called()
    assert 'SYNTHETIC_PRIVATE_ERROR' not in capsys.readouterr().err


def test_protocol_invalid_input_does_not_echo_body_or_accept_confirmed(connected):
    server, _, provider, embeddings, _ = connected
    async def check():
        async with create_connected_server_and_client_session(server) as session:
            for changes in ({'request_key': {'secret': 'SYNTHETIC_PRIVATE_INPUT'}},
                            {'confirmation_token': 'SYNTHETIC_PRIVATE_INPUT', 'confirmed': True}):
                result = await session.call_tool('recover_translation', {**ARGS, **changes})
                assert result.isError
                assert 'SYNTHETIC_PRIVATE_INPUT' not in str(result.content)
    asyncio.run(check())
    assert not provider.calls and not embeddings
