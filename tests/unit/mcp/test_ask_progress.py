"""오래 걸리는 질문 중 진행 알림(#119)."""

import asyncio
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import anyio
import pytest
from mcp import types
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session

from solo_leveling.domain.ask_execution import ExecutionOutcome, ExecutionResult
from solo_leveling.cancellation import RequestCancelled, raise_if_cancelled
from solo_leveling.interfaces.mcp import server as server_module
from tests.unit.mcp.test_server import answer_response, make_server


@pytest.fixture(autouse=True)
def fast_progress(monkeypatch):
    monkeypatch.setattr(server_module, 'PROGRESS_INTERVAL_SECONDS', 0.01)


def blocking_answer(release, *, fails=False):
    def answer(*args, **kwargs):
        if not release.wait(5):
            raise RuntimeError('TEST_RELEASE_TIMEOUT')
        if fails:
            raise RuntimeError('SYNTHETIC_PRIVATE_FAILURE')
        return answer_response()
    return answer


@pytest.mark.parametrize('fails', [False, True])
def test_progress_notifications_while_answer_runs(fails):
    release = Event()
    progress = []
    server, service, _ = make_server()
    service.answer.side_effect = blocking_answer(release, fails=fails)

    async def on_progress(value, total, message):
        progress.append((value, total, message))
        if len(progress) >= 3:
            release.set()

    async def scenario():
        with anyio.fail_after(10):
            async with create_connected_server_and_client_session(server) as client:
                result = await client.call_tool('ask_paper', {'context_id': 'context', 'question': '합성 질문'},
                                                progress_callback=on_progress)
                await client.send_ping()
                return result

    result = asyncio.run(scenario())
    values = [value for value, _, _ in progress]
    assert len(values) >= 3 and values == sorted(values) and len(set(values)) == len(values)
    assert all(total is None and message == server_module.PROGRESS_MESSAGE for _, total, message in progress)
    # 작업 예외는 그룹으로 감싸지지 않고 기존 오류 계약 그대로 나간다.
    assert result.isError is fails
    if fails:
        assert 'INTERNAL_ERROR' in result.content[0].text
        assert 'SYNTHETIC_PRIVATE_FAILURE' not in result.content[0].text
    else:
        assert result.structuredContent['status'] == 'ok'


def test_no_progress_without_token(monkeypatch):
    sent = []
    server, service, _ = make_server()
    service.answer.return_value = answer_response()

    async def scenario():
        async with create_connected_server_and_client_session(server) as client:
            original = client._received_notification

            async def record(notification):
                sent.append(notification)
                return await original(notification)

            monkeypatch.setattr(client, '_received_notification', record)
            return await client.call_tool('ask_paper', {'context_id': 'context', 'question': '합성 질문'})

    result = asyncio.run(scenario())
    assert not result.isError
    assert not any(isinstance(n.root, types.ProgressNotification) for n in sent)


def test_cancel_with_progress_token_still_stops_next_stage():
    started, release, finished = Event(), Event(), Event()
    replies, outcomes = [], []

    def answer(*args, **kwargs):
        started.set()
        try:
            if not release.wait(5):
                raise RuntimeError('TEST_RELEASE_TIMEOUT')
            raise_if_cancelled()
            outcomes.append('completed')
            return answer_response()
        except RequestCancelled:
            outcomes.append('cancelled')
            raise
        finally:
            finished.set()

    server, service, _ = make_server()
    service.answer.side_effect = answer

    async def ignore(*_):
        pass

    async def scenario():
        with anyio.fail_after(10):
            async with create_connected_server_and_client_session(server) as client:
                request_id = client._request_id

                async def invoke():
                    with pytest.raises(McpError) as error:
                        await client.call_tool('ask_paper', {'context_id': 'context', 'question': '합성 질문'},
                                               progress_callback=ignore)
                    replies.append(error.value.error.message)

                async with anyio.create_task_group() as group:
                    group.start_soon(invoke)
                    while not started.is_set():
                        await anyio.sleep(0.001)
                    await client.send_notification(types.ClientNotification(
                        types.CancelledNotification(method='notifications/cancelled',
                            params=types.CancelledNotificationParams(requestId=request_id))))
                    while not replies:
                        await anyio.sleep(0.001)
                release.set()
                while not finished.is_set():
                    await anyio.sleep(0.001)
                await client.send_ping()

    asyncio.run(scenario())
    assert replies == ['Request cancelled']
    assert outcomes == ['cancelled']


def test_submit_question_reports_progress_without_reexecuting():
    release = Event()
    progress = []
    executions = Mock()

    def run(*args, **kwargs):
        if not release.wait(5):
            raise RuntimeError('TEST_RELEASE_TIMEOUT')
        return ExecutionResult(ExecutionOutcome.IN_PROGRESS)

    executions.run.side_effect = run
    server = server_module.create_server(
        server_module.MCPServices(Mock(), Mock(), executions=executions))

    async def on_progress(value, total, message):
        progress.append((value, total, message))
        if len(progress) >= 2:
            release.set()

    async def scenario():
        with anyio.fail_after(10):
            async with create_connected_server_and_client_session(server) as client:
                result = await client.call_tool('submit_paper_question', {
                    'context_id': 'context', 'request_key': 'key', 'question': '합성 질문',
                }, progress_callback=on_progress)
                assert not result.isError
                assert result.structuredContent['state'] == 'in_progress'
                # 응답 완료 뒤에는 진행 알림 작업도 끝나야 한다.
                count = len(progress)
                await anyio.sleep(server_module.PROGRESS_INTERVAL_SECONDS * 3)
                assert len(progress) == count
                await client.send_ping()

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    values = [value for value, _, _ in progress]
    assert len(values) >= 2 and values == sorted(set(values))
    assert all(total is None and message == server_module.PROGRESS_MESSAGE
               for _, total, message in progress)
    executions.run.assert_called_once_with(
        'key', '합성 질문', context_id='context', top_k=5,
        pdf_pages=(), section_ids=(), strict_pages=False)


@pytest.mark.parametrize('fails', [False, True])
def test_notification_failure_preserves_work_result_and_original_exception(fails):
    async def scenario():
        with anyio.fail_after(5):
            sent = anyio.Event()
            original = ValueError('SYNTHETIC_PRIVATE_FAILURE')
            notifications = []

            async def report(*args):
                notifications.append(args)
                sent.set()
                raise RuntimeError('SYNTHETIC_NOTIFICATION_FAILURE')

            # 숫자 0도 유효한 진행 토큰이며 알림 전송을 생략하면 안 된다.
            context = SimpleNamespace(
                request_context=SimpleNamespace(meta=SimpleNamespace(progressToken=0)),
                report_progress=report)
            server = SimpleNamespace(get_context=lambda: context)

            async def work():
                await sent.wait()
                if fails:
                    raise original
                return 'completed'

            if fails:
                with pytest.raises(ValueError) as failure:
                    await server_module._with_progress(server, work)
                assert failure.value is original
            else:
                assert await server_module._with_progress(server, work) == 'completed'
            assert notifications == [(1, None, server_module.PROGRESS_MESSAGE)]

    asyncio.run(scenario())
