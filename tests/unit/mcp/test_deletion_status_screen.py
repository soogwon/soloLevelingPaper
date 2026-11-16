"""실제 비동기 응답 순서를 제어해 세션 전환 후 표시 누출을 검사한다."""

import asyncio
from dataclasses import replace

import pytest

from solo_leveling.interfaces.mcp.deletion_guidance import (
    DeletionLookup, DeletionSnapshot, DeletionStatusScreen,
)

LOOKUP = DeletionLookup('private-owner', 'private-paper', 'private-key')


class DeferredReader:
    def __init__(self):
        self.started = asyncio.Queue()
        self.calls = []

    async def read(self, lookup):
        self.calls.append(lookup)
        result = asyncio.get_running_loop().create_future()
        await self.started.put(result)
        return await result


def result(lookup=LOOKUP, status='complete'):
    return DeletionSnapshot(lookup, 'private-request', status, True)


def assert_safe(screen):
    text, html = screen.render()
    assert 'private-' not in text + html
    assert 'disabled' in html
    assert '<script' not in html and 'href=' not in html


@pytest.mark.parametrize('late_error', [False, True])
@pytest.mark.parametrize('next_owner', [None, LOOKUP.owner_id, 'private-other'])
def test_session_change_discards_late_success_and_failure(next_owner, late_error):
    async def scenario():
        reader = DeferredReader()
        screen = DeletionStatusScreen(reader)
        screen.session_changed(LOOKUP.owner_id)
        old = asyncio.create_task(screen.refresh(LOOKUP))
        pending = await reader.started.get()
        screen.session_changed(None)
        assert '로그인 필요' in screen.render()[0]
        if next_owner is not None:
            screen.session_changed(next_owner)
        reset = screen.render()
        if late_error:
            pending.set_exception(RuntimeError('private-error-token'))
        else:
            pending.set_result(result())
        assert await old is False
        assert screen.render() == reset
        assert '활성 저장소 정리 완료' not in reset[0]
        assert_safe(screen)
    asyncio.run(scenario())


@pytest.mark.parametrize('late_error', [False, True])
def test_new_login_result_survives_previous_session_response(late_error):
    async def scenario():
        reader = DeferredReader()
        screen = DeletionStatusScreen(reader)
        screen.session_changed(LOOKUP.owner_id)
        old = asyncio.create_task(screen.refresh(LOOKUP))
        old_response = await reader.started.get()
        # 로그아웃 알림이 합쳐져도 같은 계정의 새 세션 알림만으로 무효화한다.
        screen.session_changed(LOOKUP.owner_id)
        new = asyncio.create_task(screen.refresh(LOOKUP))
        new_response = await reader.started.get()
        new_response.set_result(result(status='cleaning'))
        assert await new is True
        current = screen.render()
        assert '자료 정리 중' in current[0]
        if late_error:
            old_response.set_exception(RuntimeError('private-error'))
        else:
            old_response.set_result(result())
        assert await old is False
        assert screen.render() == current
        assert reader.calls == [LOOKUP, LOOKUP]
        assert_safe(screen)
        screen.session_changed(None)
        assert '자료 정리 중' not in ''.join(screen.render())
    asyncio.run(scenario())


def test_latest_target_lookup_wins_without_session_change():
    async def scenario():
        reader = DeferredReader()
        screen = DeletionStatusScreen(reader)
        screen.session_changed(LOOKUP.owner_id)
        old = asyncio.create_task(screen.refresh(LOOKUP))
        old_response = await reader.started.get()
        other = replace(LOOKUP, paper_id='private-other-paper', request_key='private-other-key')
        new = asyncio.create_task(screen.refresh(other))
        new_response = await reader.started.get()
        new_response.set_result(result(other, 'failed'))
        assert await new is True
        current = screen.render()
        old_response.set_result(result())
        assert await old is False
        assert screen.render() == current
        assert '자료 정리 확인 필요' in current[0]
    asyncio.run(scenario())


def test_signed_out_and_foreign_lookup_never_call_reader():
    async def scenario():
        reader = DeferredReader()
        screen = DeletionStatusScreen(reader)
        assert await screen.refresh(LOOKUP) is True
        screen.session_changed('private-other')
        assert await screen.refresh(LOOKUP) is True
        assert reader.calls == []
        assert_safe(screen)
    asyncio.run(scenario())


@pytest.mark.parametrize('reply', [None, result(replace(LOOKUP, request_key='private-wrong')), object()])
def test_invalid_or_mismatched_current_response_cannot_claim_completion(reply):
    async def scenario():
        reader = DeferredReader()
        screen = DeletionStatusScreen(reader)
        screen.session_changed(LOOKUP.owner_id)
        task = asyncio.create_task(screen.refresh(LOOKUP))
        pending = await reader.started.get()
        pending.set_result(reply)
        assert await task is True
        assert '활성 저장소 정리 완료' not in screen.render()[0]
        assert_safe(screen)
    asyncio.run(scenario())


def test_current_error_and_cancellation_leave_no_stuck_loading_or_raw_error():
    async def scenario():
        reader = DeferredReader()
        screen = DeletionStatusScreen(reader)
        screen.session_changed(LOOKUP.owner_id)
        task = asyncio.create_task(screen.refresh(LOOKUP))
        pending = await reader.started.get()
        pending.set_exception(RuntimeError('private-error'))
        assert await task is True
        assert '삭제 상태 확인 필요' in screen.render()[0]
        task = asyncio.create_task(screen.refresh(LOOKUP))
        await reader.started.get()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert '삭제 상태 확인 필요' in screen.render()[0]
        assert_safe(screen)
    asyncio.run(scenario())


@pytest.mark.parametrize('new_completed', [False, True])
def test_old_cancellation_does_not_replace_new_session_view(new_completed):
    async def scenario():
        reader = DeferredReader()
        screen = DeletionStatusScreen(reader)
        screen.session_changed(LOOKUP.owner_id)
        old = asyncio.create_task(screen.refresh(LOOKUP))
        await reader.started.get()
        screen.session_changed(None)
        screen.session_changed(LOOKUP.owner_id)
        new = asyncio.create_task(screen.refresh(LOOKUP))
        pending = await reader.started.get()
        if new_completed:
            pending.set_result(result(status='cleaning'))
            assert await new is True
        current = screen.render()
        old.cancel()
        with pytest.raises(asyncio.CancelledError):
            await old
        assert screen.render() == current
        if not new_completed:
            pending.set_result(result(status='cleaning'))
            assert await new is True
        assert '자료 정리 중' in screen.render()[0]
    asyncio.run(scenario())


def test_rejected_lookup_clears_existing_view_and_invalidates_pending_response():
    async def scenario():
        reader = DeferredReader()
        screen = DeletionStatusScreen(reader)
        screen.session_changed(LOOKUP.owner_id)
        old = asyncio.create_task(screen.refresh(LOOKUP))
        pending = await reader.started.get()
        wrong = replace(LOOKUP, owner_id='private-other')
        assert await screen.refresh(wrong) is True
        rejected = screen.render()
        assert '삭제 요청 조회 불가' in rejected[0]
        pending.set_result(result())
        assert await old is False
        assert screen.render() == rejected
        assert reader.calls == [LOOKUP]
    asyncio.run(scenario())
