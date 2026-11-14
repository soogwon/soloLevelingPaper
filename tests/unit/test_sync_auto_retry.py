"""자동 재시도의 오류 격리와 종료 동작."""
import asyncio
from unittest.mock import Mock

import anyio

from solo_leveling.application.server_sync.retry import run_auto_retry


def test_scheduler_continues_after_error_and_stops_on_cancel():
    service = Mock()
    calls = []

    def retry():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('private')

    service.retry_due.side_effect = retry

    async def scenario():
        with anyio.fail_after(5):
            async with anyio.create_task_group() as group:
                group.start_soon(run_auto_retry, service, 0.01)
                while len(calls) < 2:
                    await anyio.sleep(0.001)
                group.cancel_scope.cancel()
            count = len(calls)
            await anyio.sleep(0.03)
            assert len(calls) == count

    asyncio.run(scenario())
