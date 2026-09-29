"""본문·예외 메시지 없이 요청별 단계 시간을 stderr에 기록한다."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import json
import faulthandler
from pathlib import Path
from threading import Lock
import sys
from time import perf_counter
from uuid import uuid4

_request = ContextVar('diagnostic_request', default=None)
_stack_directory = None
_stack_lock = Lock()


def configure_stack_diagnostics(directory):
    """전용 래퍼가 지정한 폴더에서만 지연 스택 수집을 활성화한다."""
    global _stack_directory
    _stack_directory = Path(directory)


@contextmanager
def import_watchdog(timeout=30):
    """동시 import 중 하나를 감시하고 완료 시 예약을 취소한다."""
    request_id = _request.get()
    if _stack_directory is None or request_id is None:
        yield
        return
    span_id = uuid4().hex
    if not _stack_lock.acquire(blocking=False):
        _emit(request_id, span_id, 'import_stack_watchdog', 'error', 0, 'WATCHDOG_BUSY')
        yield
        return
    output = None
    armed = False
    try:
        try:
            path = _stack_directory / f'import-stack-{request_id}-{span_id}.log'
            output = path.open('x', encoding='utf-8')
            faulthandler.dump_traceback_later(timeout, repeat=False, file=output, exit=False)
            armed = True
        except Exception:
            _emit(request_id, span_id, 'import_stack_watchdog', 'error', 0, 'WATCHDOG_SETUP_FAILED')
        yield
    finally:
        try:
            if armed:
                faulthandler.cancel_dump_traceback_later()
        finally:
            if output is not None:
                output.close()
            _stack_lock.release()


def _emit(request_id, span_id, stage, event, elapsed_ms, error_code=None):
    # 인자·반환값·예외 문자열을 받지 않는 고정 필드 출력이다.
    record = dict(request_id=request_id, span_id=span_id, stage=stage,
                  event=event, elapsed_ms=elapsed_ms, error_code=error_code)
    try:
        sys.stderr.write(json.dumps(record) + '\n')
        sys.stderr.flush()
    except Exception:
        # 진단 출력 실패가 실제 요청 결과를 바꾸지 않도록 한다.
        pass


@contextmanager
def stage(name):
    request_id = _request.get()
    if request_id is None:
        yield
        return
    span_id = uuid4().hex
    started = perf_counter()
    _emit(request_id, span_id, name, 'start', 0)
    try:
        yield
    except BaseException as error:
        code = 'CANCELLED' if isinstance(error, (KeyboardInterrupt, GeneratorExit)) or type(error).__name__ == 'CancelledError' else 'STAGE_FAILED'
        _emit(request_id, span_id, name, 'error', round((perf_counter() - started) * 1000, 3), code)
        raise
    else:
        _emit(request_id, span_id, name, 'end', round((perf_counter() - started) * 1000, 3))


def traced(name, *, request=False):
    """동기·비동기 호출을 계측한다. 요청 ID는 본문과 무관하게 생성한다."""
    def decorate(function):
        @contextmanager
        def boundary():
            token = _request.set(uuid4().hex) if request and _request.get() is None else None
            try:
                with stage(name):
                    yield
            finally:
                if token is not None:
                    _request.reset(token)

        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def asynchronous(*args, **kwargs):
                with boundary():
                    return await function(*args, **kwargs)
            return asynchronous

        @wraps(function)
        def synchronous(*args, **kwargs):
            with boundary():
                return function(*args, **kwargs)
        return synchronous
    return decorate
