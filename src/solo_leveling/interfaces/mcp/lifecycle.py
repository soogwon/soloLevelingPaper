"""서버가 소유한 공유 자원을 마지막 사용자 종료 후 한 번만 정리한다."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import sys
import json
import threading

from solo_leveling.infrastructure.storage.vector_store import close_client, ChromaCloseResult
from .ingestion import IngestionShuttingDownError


_lifetimes = ContextVar('runtime_lifetimes', default=None)


def track_lifetime(lifetime):
    owners = _lifetimes.get()
    if owners is not None:
        owners.append(lifetime)


@contextmanager
def cleanup_scope():
    """진단 스트림을 복원하기 전에 지연된 정리까지 기다린다."""
    owners = []
    token = _lifetimes.set(owners)
    try:
        yield
    finally:
        try:
            for owner in owners:
                owner.begin()
            for owner in owners:
                owner.done.wait()
        finally:
            _lifetimes.reset(token)


def report(code):
    """원본 예외나 경로 없이 고정된 종료 코드만 출력한다."""
    try:
        print(json.dumps({'event': 'runtime_cleanup', 'code': code}), file=sys.stderr, flush=True)
    except Exception:
        pass


class RequestGate:
    def __init__(self):
        self.condition = threading.Condition()
        self.accepting = True
        self.active = 0

    @contextmanager
    def use(self):
        with self.condition:
            if not self.accepting:
                raise IngestionShuttingDownError()
            self.active += 1
        try:
            yield
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()

    def stop(self):
        with self.condition:
            self.accepting = False

    def wait(self):
        with self.condition:
            self.condition.wait_for(lambda: self.active == 0)

    def wrap(self, target):
        return GuardedService(target, self)


class GuardedService:
    """동기 서비스 호출 전체를 추적하여 검색·생성 도중 조기 종료를 막는다."""
    def __init__(self, target, gate):
        self.target = target
        self.gate = gate

    def __getattr__(self, name):
        method = getattr(self.target, name)
        if not callable(method):
            return method

        @wraps(method)
        def guarded(*args, **kwargs):
            with self.gate.use():
                return method(*args, **kwargs)
        return guarded


class RuntimeLifetime:
    def __init__(self, gate, ingestion, embedder, client):
        self.gate, self.ingestion = gate, ingestion
        self.embedder, self.client = embedder, client
        self.lock = threading.Lock()
        self.started = False
        self.done = threading.Event()
        self.close_result = None

    def begin(self):
        """반복 호출을 허용하고 비동기 루프 밖에서 종료를 기다린다."""
        with self.lock:
            if self.started:
                return
            self.gate.stop()
            self.started = True
            threading.Thread(target=self._finish, name='mcp-resource-cleanup', daemon=False).start()

    def _finish(self):
        try:
            self.ingestion.begin_shutdown()
            self.gate.wait()
            # 호출이 반환된 뒤에도 실행 중인 등록 작업까지 모두 기다린다.
            if not self.ingestion.wait_for_idle(None):
                raise RuntimeError('RUNTIME_DRAIN_FAILED')
        except Exception:
            # 사용 종료를 증명하지 못했으면 공유 자원을 닫지 않는다.
            report('RUNTIME_DRAIN_FAILED')
            self.done.set()
            return
        try:
            self.embedder.close()
        except Exception:
            report('EMBEDDER_CLOSE_FAILED')
        try:
            self.close_result = close_client(self.client)
            if self.close_result not in (ChromaCloseResult.CLOSED, ChromaCloseResult.ALREADY_CLOSED):
                report(self.close_result.value)
        finally:
            self.done.set()
