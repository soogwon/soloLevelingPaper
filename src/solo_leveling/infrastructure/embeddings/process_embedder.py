"""임베딩 전용 프로세스를 재사용하고 대기·계산 시간을 제한한다."""

from contextlib import contextmanager
from enum import Enum
import math
import multiprocessing
import json
import os
from pathlib import Path
import sys
import tempfile
from queue import Queue, Empty
from threading import Lock, Thread
from time import monotonic
from uuid import uuid4

from solo_leveling.diagnostics import _request, stage
from .worker import run_worker, diagnostic_worker


class EmbeddingFailure(str, Enum):
    TIMEOUT = 'EMBEDDING_TIMEOUT'
    UNAVAILABLE = 'EMBEDDING_UNAVAILABLE'
    INVALID_RESULT = 'EMBEDDING_INVALID_RESULT'
    CLOSED = 'EMBEDDING_CLOSED'


class EmbeddingProcessError(RuntimeError):
    def __init__(self, code: EmbeddingFailure):
        self.code = code
        super().__init__(code.value)


@contextmanager
def _null_stdin_for_child():
    """Windows 자식은 부모 표준 핸들을 물려받아 부팅 중 검사한다. MCP stdio처럼
    부모가 stdin 파이프를 블로킹 읽기 중이면 자식 부팅이 멈추므로 생성 동안만 NUL로 바꾼다.
    부모의 stdin 읽기는 CRT fd 0 핸들을 쓰므로 영향을 받지 않는다."""
    if os.name != 'nt':
        yield
        return
    import ctypes
    import msvcrt
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.GetStdHandle.restype = ctypes.c_void_p
    kernel32.GetStdHandle.argtypes = [ctypes.c_uint32]
    kernel32.SetStdHandle.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    std_input = 0xFFFFFFF6  # STD_INPUT_HANDLE (-10)
    with open(os.devnull, 'rb') as null:
        previous = kernel32.GetStdHandle(std_input)
        swapped = bool(kernel32.SetStdHandle(std_input, msvcrt.get_osfhandle(null.fileno())))
        try:
            yield
        finally:
            if swapped:
                kernel32.SetStdHandle(std_input, previous)


class ProcessEmbedder:
    def __init__(self, timeout_seconds=90, *, worker_target=run_worker, log_dir=None):
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError('임베딩 제한 시간은 유한한 양수여야 합니다.')
        self.timeout = timeout_seconds
        self._target = worker_target
        self._serial = Lock()
        self._state = Lock()
        self._closed = False
        self._process = None
        self._connection = None
        self._log_dir = str(Path(log_dir or Path(tempfile.gettempdir()) / 'solo-leveling-diagnostics').resolve())
        self._worker_id = None

    def _event(self, event, *, pid=None, exit_code=None, alive=None):
        try:
            print(json.dumps(dict(event=event, worker_id=self._worker_id,
                request_id=_request.get(), pid=pid, exit_code=exit_code, alive=alive)),
                file=sys.stderr, flush=True)
        except Exception:
            pass

    def _stop(self):
        """상태 잠금을 잡은 호출자만 사용한다. 자신이 생성한 프로세스만 종료한다."""
        process, connection = self._process, self._connection
        self._process = self._connection = None
        if process is not None and process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            if not process.is_alive():
                self._event('worker_stopped', pid=process.pid, exit_code=process.exitcode, alive=False)
                process.close()
            else:
                self._event('worker_stop_failed', pid=process.pid, alive=True)
        if connection is not None:
            connection.close()

    def close(self):
        with self._state:
            self._closed = True
            self._stop()

    def __call__(self, texts, *, model_name):
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError('모델 이름이 필요합니다.')
        if not isinstance(texts, (list, tuple)) or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError('비어 있지 않은 문자열 목록이 필요합니다.')
        if not texts:
            return []
        deadline = monotonic() + self.timeout
        with stage('embedding_process_wait'):
            acquired = self._serial.acquire(timeout=self.timeout)
        if not acquired:
            # 다른 요청 소유의 실행 중 프로세스를 종료하지 않는다.
            raise EmbeddingProcessError(EmbeddingFailure.TIMEOUT)
        io_thread = None
        try:
            with self._state:
                if self._closed:
                    raise EmbeddingProcessError(EmbeddingFailure.CLOSED)
                if self._process is None or not self._process.is_alive():
                    self._stop()
                    context = multiprocessing.get_context('spawn')
                    self._connection, child = context.Pipe()
                    self._worker_id = uuid4().hex
                    self._process = context.Process(target=diagnostic_worker,
                        args=(child, self._target, self._log_dir, self._worker_id, min(30, self.timeout / 2)), daemon=True)
                    try:
                        self._event('worker_spawn_start')
                        with _null_stdin_for_child():
                            self._process.start()
                        self._event('worker_spawn_return', pid=self._process.pid)
                    finally:
                        child.close()
                connection = self._connection
            request = {'texts': list(texts), 'model': model_name,
                       'request_id': _request.get() or uuid4().hex}
            completed = Queue(maxsize=1)

            def exchange():
                try:
                    connection.send(request)
                    completed.put((True, connection.recv()))
                except Exception:
                    completed.put((False, None))

            # 큰 IPC 쓰기·읽기도 호출 스레드를 무제한 붙잡지 않는다.
            io_thread = Thread(target=exchange, daemon=True)
            io_thread.start()
            with stage('embedding_process_response'):
                try:
                    success, result = completed.get(timeout=max(0, deadline - monotonic()))
                except Empty:
                    raise EmbeddingProcessError(EmbeddingFailure.TIMEOUT) from None
                if not success or not isinstance(result, dict) or result.get('ok') is not True:
                    raise EmbeddingProcessError(EmbeddingFailure.UNAVAILABLE)
                vectors, dimension = result.get('vectors'), result.get('dimension')
                if (type(dimension) is not int or dimension <= 0 or not isinstance(vectors, list)
                        or len(vectors) != len(texts)
                        or any(not isinstance(v, list) or len(v) != dimension
                               or any(type(x) not in (float, int) or not math.isfinite(x) for x in v)
                               for v in vectors)):
                    raise EmbeddingProcessError(EmbeddingFailure.INVALID_RESULT)
                return vectors
        except BaseException as error:
            with self._state:
                self._stop()
            if isinstance(error, (EmbeddingProcessError, KeyboardInterrupt, SystemExit)):
                raise
            raise EmbeddingProcessError(EmbeddingFailure.UNAVAILABLE) from None
        finally:
            if io_thread is not None:
                io_thread.join(timeout=1)
            self._serial.release()
