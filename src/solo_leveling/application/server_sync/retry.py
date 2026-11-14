"""기존 승인 작업의 재시도만 주기적으로 실행한다."""

import anyio


async def run_auto_retry(service, interval_seconds=30):
    while True:
        await anyio.sleep(interval_seconds)
        try:
            # 런타임 gate가 실행 중인 스레드의 수명을 추적한다.
            await anyio.to_thread.run_sync(service.retry_due, abandon_on_cancel=True)
        except Exception:
            # 원본 예외나 사용자 데이터를 로그로 내보내지 않는다.
            continue
