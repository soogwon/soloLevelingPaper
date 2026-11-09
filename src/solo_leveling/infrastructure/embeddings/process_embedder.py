"""임베딩 전용 프로세스를 재사용하고 대기·계산 시간을 제한한다."""

from contextlib import contextmanager
from datetime import datetime, timezone
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

from solo_leveling.diagnostics import _request, report_runtime_cleanup, stage
from .worker import run_worker, diagnostic_worker, raw_logs_requested


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
    def __init__(self, timeout_seconds=90, *, worker_target=run_worker, log_dir=None,
                 collect_raw_logs=None):
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError('임베딩 제한 시간은 유한한 양수여야 합니다.')
        self.timeout = timeout_seconds
        self._target = worker_target
        self._serial = Lock()
        self._state = Lock()
        self._closed = False
        self._process = None
        self._connection = None
        self._stop_unconfirmed = False
        self._pending_closes = []
        self._log_dir = str(Path(log_dir or Path(tempfile.gettempdir()) / 'solo-leveling-diagnostics').resolve())
        self._worker_id = None
        self._log_store = None
        self._log_lease = None
        self._log_files = ()
        # None이면 환경 설정을 따르고, 기본은 원본 로그·스택을 수집하지 않는다.
        self._collect_raw = raw_logs_requested() if collect_raw_logs is None else collect_raw_logs is True

    def _prepare_worker_logs(self):
        """부모가 잠금·원본 참조·자식에게 넘길 쓰기 스트림을 소유한다."""
        try:
            from solo_leveling.infrastructure.log_retention.contracts import BundleId, ClosedFile
            if os.name == 'nt':
                from solo_leveling.infrastructure.log_retention.windows import WindowsBundleStore
                store = WindowsBundleStore()
            elif os.name == 'posix':
                from solo_leveling.infrastructure.log_retention.posix import PosixBundleStore
                store = PosixBundleStore()
            else:
                return False
            acquired = store.try_acquire_writer(Path(self._log_dir), BundleId('worker', self._worker_id))
            if acquired.lease is None:
                return False
            lease = acquired.lease
            files = []
            outputs = []
            try:
                for kind in (('raw', 'stack') if self._collect_raw else ('safe',)):
                    path, output = lease.open_log(kind)
                    output.flush()
                    os.fsync(output.fileno())
                    files.append(ClosedFile(kind, path.name, 0))
                    outputs.append((kind, output))
            except Exception:
                lease.release()
                return False
            self._log_store, self._log_lease, self._log_files = store, lease, tuple(files)
            return tuple(outputs)
        except Exception:
            return False

    def _send_worker_logs(self, connection, process):
        """검증한 열린 파일만 OS 핸들 전달로 자식에 넘긴다. 경로는 보내지 않는다."""
        outputs = self._prepare_worker_logs()
        if outputs is False:
            try:
                connection.send((False, ()))
            except Exception:
                pass
            return
        try:
            from multiprocessing.reduction import send_handle
            connection.send((True, tuple(kind for kind, _ in outputs)))
            for _, output in outputs:
                handle = output.fileno()
                if os.name == 'nt':
                    import msvcrt
                    handle = msvcrt.get_osfhandle(handle)
                send_handle(connection, handle, process.pid)
        except Exception:
            # 자식은 불완전한 전달을 감지해 받은 핸들을 닫고 수집 없이 계속한다.
            pass
        finally:
            for _, output in outputs:
                try:
                    output.close()
                except OSError:
                    pass

    def _finish_worker_logs(self, *, publish):
        """자식 종료 확인 뒤에만 크기를 다시 읽고 종료 기록을 게시한다."""
        store, lease, files = self._log_store, self._log_lease, self._log_files
        self._log_store = self._log_lease = None
        self._log_files = ()
        if lease is None:
            return
        try:
            if publish:
                from solo_leveling.infrastructure.log_retention.contracts import ClosedFile
                current = tuple(ClosedFile(item.kind, item.name,
                    (lease.root / item.name).stat().st_size) for item in files)
                store.publish_closed(lease, current, datetime.now(timezone.utc))
        except Exception:
            pass
        finally:
            try:
                lease.release()
            except Exception:
                pass

    def _event(self, event, *, pid=None, exit_code=None, alive=None):
        try:
            print(json.dumps(dict(event=event, worker_id=self._worker_id,
                request_id=_request.get(), pid=pid, exit_code=exit_code, alive=alive)),
                file=sys.stderr, flush=True)
        except Exception:
            pass

    def _record_close_failure(self, handle):
        # Pipe는 하위 닫기에서 예외가 나도 핸들을 비운다. 원시 번호는 재사용될 수 있다.
        try:
            closed = getattr(handle, 'closed', None)
            if closed is None:
                # Process에는 공개 closed 속성이 없다. 종료 콜백이 남아 있어야 재시도할 수 있다.
                finalizer = getattr(getattr(handle, '_popen', None), 'finalizer', None)
                retryable = finalizer is not None and finalizer.still_active()
            else:
                retryable = closed is False
        except Exception:
            # 상태 판별도 실패하면 해제됐을 수 있는 원시 번호를 다시 닫지 않는다.
            report_runtime_cleanup('WORKER_HANDLE_RELEASE_UNCONFIRMED')
            return
        if retryable:
            self._pending_closes.append(handle)
        else:
            report_runtime_cleanup('WORKER_HANDLE_RELEASE_UNCONFIRMED')

    def _close_handle(self, handle):
        try:
            handle.close()
        except Exception:
            self._record_close_failure(handle)

    def _stop(self):
        """상태 잠금을 잡은 호출자만 사용한다. 자신이 생성한 프로세스만 종료한다."""
        process, connection = self._process, self._connection
        self._connection = None

        def alive():
            try:
                return process.is_alive()
            except Exception:
                return None

        pending, self._pending_closes = self._pending_closes, []
        for handle in pending:
            self._close_handle(handle)

        try:
            if process is None:
                self._stop_unconfirmed = False
                self._finish_worker_logs(publish=False)
                return
            try:
                pid = process.pid
            except Exception:
                pid = None
            else:
                if pid is None:
                    self._close_handle(process)
                    self._process = None
                    self._stop_unconfirmed = False
                    self._finish_worker_logs(publish=False)
                    return

            # 이전 종료 시도에서 생존 여부를 확정하지 못한 worker에는 종료 API를 반복 호출하지
            # 않는다. 자연 종료 여부만 다시 확인해 회수하거나 계속 격리한다.
            if self._stop_unconfirmed:
                final_alive = alive()
            else:
                current_alive = alive()
                if current_alive is True:
                    try:
                        process.terminate()
                    except Exception:
                        pass
                try:
                    process.join(timeout=1)
                except Exception:
                    pass
                current_alive = alive()
                if current_alive is True:
                    try:
                        process.kill()
                    except Exception:
                        pass
                    try:
                        process.join(timeout=1)
                    except Exception:
                        pass
                final_alive = alive()

            if final_alive is False:
                try:
                    exit_code = process.exitcode
                except Exception:
                    exit_code = None
                self._event('worker_stopped', pid=pid, exit_code=exit_code, alive=False)
                self._close_handle(process)
                self._process = None
                self._stop_unconfirmed = False
                self._finish_worker_logs(publish=True)
            else:
                # 살아 있거나 상태 확인이 실패한 프로세스의 마지막 소유 참조를 보존한다.
                self._process = process
                self._stop_unconfirmed = True
                self._event('worker_stop_failed', pid=pid, alive=final_alive)
        finally:
            if connection is not None:
                self._close_handle(connection)

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
                if self._stop_unconfirmed:
                    self._stop()
                    if self._stop_unconfirmed:
                        raise EmbeddingProcessError(EmbeddingFailure.UNAVAILABLE)
                if self._process is None or not self._process.is_alive():
                    self._stop()
                    if self._stop_unconfirmed:
                        raise EmbeddingProcessError(EmbeddingFailure.UNAVAILABLE)
                    context = multiprocessing.get_context('spawn')
                    self._connection, child = context.Pipe()
                    # POSIX의 SCM_RIGHTS 핸들 전달에는 Unix socket 기반 duplex Pipe가 필요하다.
                    log_receiver, log_sender = context.Pipe()
                    self._worker_id = uuid4().hex
                    self._process = context.Process(target=diagnostic_worker,
                        args=(child, self._target, self._log_dir, self._worker_id, min(30, self.timeout / 2),
                              self._collect_raw, None, log_receiver), daemon=True)
                    try:
                        self._event('worker_spawn_start')
                        with _null_stdin_for_child():
                            self._process.start()
                        log_receiver.close()
                        self._send_worker_logs(log_sender, self._process)
                        self._event('worker_spawn_return', pid=self._process.pid)
                    finally:
                        for handle in (log_receiver, log_sender):
                            try:
                                handle.close()
                            except Exception:
                                self._record_close_failure(handle)
                        try:
                            child.close()
                        except Exception:
                            self._record_close_failure(child)
                            raise
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
