"""임베딩 라이브러리는 이 전용 프로세스의 메인 스레드에서만 로딩한다."""

import os
import sys
import faulthandler
import json
from pathlib import Path

_stack_output = None
_watchdog_seconds = 30


def diagnostic_worker(connection, target, log_dir, worker_id, watchdog_seconds):
    """모델 관련 import보다 먼저 별도 로그를 열고 자식 진입을 기록한다."""
    global _stack_output, _watchdog_seconds
    _watchdog_seconds = watchdog_seconds
    try:
        folder = Path(log_dir)
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f'worker-{worker_id}.log').open('x', encoding='utf-8') as output:
            os.dup2(output.fileno(), 2)
        sys.stderr = os.fdopen(os.dup(2), 'w', encoding='utf-8', buffering=1)
        os.dup2(2, 1)
        sys.stdout = sys.stderr
        print(json.dumps({'event': 'worker_enter', 'worker_id': worker_id, 'pid': os.getpid()}), flush=True)
        with (folder / f'worker-stack-{worker_id}.log').open('x', encoding='utf-8') as stack:
            _stack_output = stack
            faulthandler.dump_traceback_later(watchdog_seconds, file=stack, exit=False)
            try:
                target(connection)
            finally:
                faulthandler.cancel_dump_traceback_later()
                _stack_output = None
    except Exception:
        # 실패의 본문은 로그나 IPC로 내보내지 않는다.
        try:
            connection.send({'ok': False})
        except Exception:
            pass
    finally:
        connection.close()


def run_worker(connection):
    # 라이브러리 출력이 MCP stdout에 섞이지 않도록 파일 설명자까지 분리한다.
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    from solo_leveling.diagnostics import _request, stage
    from solo_leveling.infrastructure.embeddings import embedder
    if _stack_output is not None:
        faulthandler.cancel_dump_traceback_later()
    previous_model = None
    try:
        while True:
            request = connection.recv()
            token = _request.set(request['request_id'])
            try:
                if _stack_output is not None:
                    faulthandler.dump_traceback_later(_watchdog_seconds, file=_stack_output, exit=False)
                with stage('worker_request_received'):
                    pass
                # 여러 모델을 번갈아 사용할 때 메모리에 계속 쌓지 않는다.
                if previous_model != request['model']:
                    embedder._model_cache.clear()
                    previous_model = request['model']
                with stage('embedding_worker'):
                    vectors = embedder.embed_texts(request['texts'], model_name=request['model'])
                    dimension = embedder.embedding_dimension(request['model'])
                with stage('worker_result_send'):
                    connection.send({'ok': True, 'vectors': vectors, 'dimension': dimension})
            except Exception:
                # 예외 메시지·본문·경로를 IPC 응답으로 넘기지 않는다.
                connection.send({'ok': False})
                return
            finally:
                if _stack_output is not None:
                    faulthandler.cancel_dump_traceback_later()
                _request.reset(token)
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()
