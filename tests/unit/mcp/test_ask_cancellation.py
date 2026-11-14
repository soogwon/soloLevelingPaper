"""호스트 대기 종료와 동기 답변 작업 종료가 다름을 실제 MCP 호출 경로로 재현한다."""

import asyncio
import json
from threading import Event

import anyio
import pytest
from mcp import types
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session

from solo_leveling.cancellation import RequestCancelled, cancellation_signal, raise_if_cancelled
from tests.unit.mcp.test_server import answer_response, make_server


async def wait_event(event):
    # 느린 API 대신 이벤트로 순서를 제어하고 실패한 테스트도 무한 대기하지 않는다.
    async with asyncio.timeout(5):
        while not event.is_set():
            await asyncio.sleep(0.001)


@pytest.mark.parametrize('worker_fails', [False, True])
def test_wait_expiry_without_cancel_keeps_request_running(worker_fails, capsys):
    """취소 알림 없이 대기만 끝나면 작업과 후속 응답은 계속된다."""
    started, release, finished = Event(), Event(), Event()
    effects = []
    server, service, _ = make_server()

    def answer(*args, **kwargs):
        started.set()
        if not release.wait(5):
            raise RuntimeError('TEST_RELEASE_TIMEOUT')
        effects.append('simulated_save')
        finished.set()
        if worker_fails:
            raise RuntimeError('SYNTHETIC_PRIVATE_WORKER_FAILURE')
        return answer_response()

    service.answer.side_effect = answer

    async def scenario():
        with anyio.fail_after(10):
            async with create_connected_server_and_client_session(server) as client:
                task = asyncio.create_task(client.call_tool('ask_paper',
                    {'context_id': 'context', 'question': '합성 질문'}))
                try:
                    await wait_event(started)
                    # Desktop 내부 구현 대신 관찰된 '대기만 종료'를 재현한다.
                    # asyncio.wait는 시간 초과 시 대상 작업을 취소하지 않는다.
                    done, pending = await asyncio.wait({task}, timeout=0)
                    assert not done and pending == {task}
                    assert not finished.is_set()
                    assert service.answer.call_count == 1
                finally:
                    release.set()
                result = await task
                assert finished.is_set()
                assert effects == ['simulated_save']
                assert result.isError is worker_fails
                if worker_fails:
                    assert 'INTERNAL_ERROR' in result.content[0].text
                    assert 'SYNTHETIC_PRIVATE_WORKER_FAILURE' not in result.content[0].text
                else:
                    assert result.structuredContent['status'] == 'ok'
                await client.send_ping()
                service.answer.side_effect = None
                service.answer.return_value = answer_response()
                # 같은 입력의 재질문도 현 계약에서는 별도의 정상 호출이다.
                result = await client.call_tool('ask_paper',
                    {'context_id': 'context', 'question': '합성 질문'})
                assert not result.isError
                assert service.answer.call_count == 2

    asyncio.run(scenario())
    logs = capsys.readouterr().err
    assert 'SYNTHETIC_PRIVATE_WORKER_FAILURE' not in logs
    records = [json.loads(line) for line in logs.splitlines() if line.startswith('{')]
    assert not any(record.get('error_code') == 'CANCELLED' for record in records)


def test_task_cancellation_does_not_stop_answer_worker():
    started, release, finished = Event(), Event(), Event()
    effects = []

    def answer(*args, **kwargs):
        started.set()
        if not release.wait(5):
            raise RuntimeError('TEST_RELEASE_TIMEOUT')
        effects.append('simulated_save')
        finished.set()
        return answer_response()

    server, service, _ = make_server()
    service.answer.side_effect = answer

    async def scenario():
        task = asyncio.create_task(server.call_tool('ask_paper',
            {'context_id': 'context', 'question': '합성 질문'}))
        try:
            await wait_event(started)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert task.cancelled()
            assert not finished.is_set()
        finally:
            release.set()
            await wait_event(finished)
        assert effects == ['simulated_save']
        assert service.answer.call_count == 1

    asyncio.run(scenario())


@pytest.mark.parametrize('worker_fails', [False, True])
def test_mcp_cancel_notification_replies_before_worker_stops(worker_fails, capsys):
    """실제 MCP 세션에 취소 알림을 보내도 동기 작업의 부수 효과는 남는다."""
    started, release, finished = Event(), Event(), Event()
    replies = []
    effects = []

    def answer(*args, **kwargs):
        started.set()
        if not release.wait(5):
            raise RuntimeError('TEST_RELEASE_TIMEOUT')
        effects.append('simulated_save')
        finished.set()
        if worker_fails:
            raise RuntimeError('SYNTHETIC_PRIVATE_WORKER_FAILURE')
        return answer_response()

    server, service, _ = make_server()
    service.answer.side_effect = answer

    async def scenario():
        with anyio.fail_after(10):
            async with create_connected_server_and_client_session(server) as client:
                # 초기화 뒤 다음 요청 ID를 확보한다. 테스트 안에서 다른 요청은 보내지 않는다.
                request_id = client._request_id

                async def invoke():
                    with pytest.raises(McpError) as error:
                        await client.call_tool('ask_paper',
                            {'context_id': 'context', 'question': '합성 질문'})
                    replies.append(error.value.error.message)

                async with anyio.create_task_group() as group:
                    group.start_soon(invoke)
                    try:
                        await wait_event(started)
                        await client.send_notification(types.ClientNotification(
                            types.CancelledNotification(method='notifications/cancelled',
                                params=types.CancelledNotificationParams(requestId=request_id))))
                        with anyio.fail_after(3):
                            while not replies:
                                await anyio.sleep(0.001)
                        assert replies == ['Request cancelled']
                        assert not finished.is_set()
                    finally:
                        release.set()
                        await wait_event(finished)
                assert effects == ['simulated_save']
                assert service.answer.call_count == 1
                # 취소 응답 뒤 정상 응답을 다시 보내 세션이 죽지 않는지도 확인한다.
                await client.send_ping()
                service.answer.side_effect = None
                service.answer.return_value = answer_response()
                result = await client.call_tool('ask_paper',
                    {'context_id': 'context', 'question': '다음 합성 질문'})
                assert not result.isError
                assert result.structuredContent['status'] == 'ok'

    asyncio.run(scenario())
    logs = capsys.readouterr().err
    assert 'SYNTHETIC_PRIVATE_WORKER_FAILURE' not in logs
    records = [json.loads(line) for line in logs.splitlines() if line.startswith('{')]
    assert any(record.get('error_code') == 'CANCELLED' for record in records)


def test_cancel_scope_returns_without_waiting_and_stops_before_next_stage():
    """취소되면 작업 종료를 기다리지 않고, 작업은 다음 단계를 시작하기 전에 멈춘다."""
    started, release, finished = Event(), Event(), Event()
    returned, effects, outcomes = [], [], []
    scopes = []

    def answer(*args, **kwargs):
        started.set()
        try:
            if not release.wait(5):
                raise RuntimeError('TEST_RELEASE_TIMEOUT')
            # 진행 중이던 단계가 끝난 뒤 다음 단계(예: 생성 API) 직전 확인.
            raise_if_cancelled()
            effects.append('next_stage')
            outcomes.append('completed')
            return answer_response()
        except RequestCancelled:
            outcomes.append('cancelled')
            raise
        finally:
            finished.set()

    server, service, _ = make_server()
    service.answer.side_effect = answer

    async def invoke():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            returned.append(await server.call_tool('ask_paper',
                {'context_id': 'context', 'question': '합성 질문'}))

    async def scenario():
        with anyio.fail_after(5):
            async with anyio.create_task_group() as group:
                group.start_soon(invoke)
                await wait_event(started)
                scopes[0].cancel()
            # 작업 스레드가 아직 막혀 있어도 호출은 이미 끝났다.
            assert not finished.is_set()
            assert not returned
            release.set()
            await wait_event(finished)
        assert outcomes == ['cancelled']
        assert effects == []
        assert service.answer.call_count == 1

    asyncio.run(scenario())


def test_mcp_cancel_notification_stops_worker_before_next_stage():
    """실제 MCP 취소 알림이 작업 스레드까지 전달되고 세션은 계속 쓸 수 있다."""
    started, release, finished = Event(), Event(), Event()
    replies, effects, outcomes = [], [], []

    def answer(*args, **kwargs):
        started.set()
        try:
            if not release.wait(5):
                raise RuntimeError('TEST_RELEASE_TIMEOUT')
            raise_if_cancelled()
            effects.append('next_stage')
            outcomes.append('completed')
            return answer_response()
        except RequestCancelled:
            outcomes.append('cancelled')
            raise
        finally:
            finished.set()

    server, service, _ = make_server()
    service.answer.side_effect = answer

    async def scenario():
        with anyio.fail_after(10):
            async with create_connected_server_and_client_session(server) as client:
                request_id = client._request_id

                async def invoke():
                    with pytest.raises(McpError) as error:
                        await client.call_tool('ask_paper',
                            {'context_id': 'context', 'question': '합성 질문'})
                    replies.append(error.value.error.message)

                async with anyio.create_task_group() as group:
                    group.start_soon(invoke)
                    await wait_event(started)
                    await client.send_notification(types.ClientNotification(
                        types.CancelledNotification(method='notifications/cancelled',
                            params=types.CancelledNotificationParams(requestId=request_id))))
                    with anyio.fail_after(3):
                        while not replies:
                            await anyio.sleep(0.001)
                assert replies == ['Request cancelled']
                release.set()
                await wait_event(finished)
                assert outcomes == ['cancelled'] and effects == []
                await client.send_ping()
                service.answer.side_effect = None
                service.answer.return_value = answer_response()
                result = await client.call_tool('ask_paper',
                    {'context_id': 'context', 'question': '다음 합성 질문'})
                assert not result.isError
                assert result.structuredContent['status'] == 'ok'

    asyncio.run(scenario())


def test_cancellation_signal_is_scoped_to_its_request():
    """취소 신호는 해당 요청 문맥 안에서만 보이고, 기본 답변 단계 확인이 그 신호를 쓴다."""
    from solo_leveling.application.evidence_qa.answer import AnswerService

    AnswerService.checkpoint()
    with cancellation_signal() as cancelled:
        AnswerService.checkpoint()
        cancelled.set()
        with pytest.raises(RequestCancelled):
            AnswerService.checkpoint()
    AnswerService.checkpoint()
