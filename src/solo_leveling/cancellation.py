"""호출자가 기다리기를 그만둔 동기 작업을 단계 사이에서 멈추게 한다(#119).

실행 중인 API 호출이나 저장을 끊지는 않는다. 다음 단계를 시작하기 전에만 확인한다.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event


class RequestCancelled(Exception):
    """호출자가 취소해 다음 단계를 시작하지 않았다."""


_cancelled: ContextVar[Event | None] = ContextVar('request_cancelled', default=None)


@contextmanager
def cancellation_signal():
    """이 문맥에서 시작한 작업 스레드가 볼 취소 신호를 만든다. AnyIO 스레드는 문맥을 물려받는다."""
    signal = Event()
    token = _cancelled.set(signal)
    try:
        yield signal
    finally:
        _cancelled.reset(token)


def raise_if_cancelled():
    signal = _cancelled.get()
    if signal is not None and signal.is_set():
        raise RequestCancelled()
