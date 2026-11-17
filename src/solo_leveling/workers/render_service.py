"""Render 검증 worker: 기본 대기, 승인된 대상 한 번 실행, 종료 시 자식 회수."""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading


def emit(status):
    print(json.dumps({'status': status}), flush=True)


def supervise(stop, command=None, *, preflight=None, poll_seconds=0.2, grace_seconds=5):
    child = None
    try:
        if preflight is not None:
            child = subprocess.Popen(preflight, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
            while child.poll() is None:
                if stop.wait(poll_seconds): return 0
            if child.returncode != 0:
                emit('render_preflight_failed')
                return 1
            child = None
        if stop.is_set(): return 0
        emit('render_ready')
        if command is not None and not stop.is_set():
            child = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
            emit('render_verification_started')
            while child.poll() is None:
                if stop.wait(poll_seconds): return 0
            # 완료 후 재시작하지 않는다. DB의 attempt 제한도 그대로 유지된다.
            emit('render_verification_finished' if child.returncode == 0 else 'render_verification_failed')
            child = None
        emit('render_standby')
        stop.wait()
        return 0
    except Exception:
        emit('render_service_failed')
        return 1
    finally:
        if child is not None:
            try:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=grace_seconds)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait(timeout=grace_seconds)
            except Exception:
                emit('render_cleanup_failed')
                return 1
        emit('render_stopped')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('standby', 'verify-once', 'question-worker'), default='standby')
    parser.add_argument('--config')
    parser.add_argument('--runtime', help='검토한 질문 서버 runtime module:factory')
    parser.add_argument('--max-jobs', type=int, default=1)
    args = parser.parse_args(argv)
    if args.mode == 'question-worker':
        import re
        if args.config or not args.runtime or not re.fullmatch(r'[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*:[a-zA-Z_]\w*', args.runtime) or not 1 <= args.max_jobs <= 10000:
            parser.error('질문 runtime과 유효한 작업 상한이 필요합니다.')
    elif args.runtime or args.max_jobs != 1:
        parser.error('질문 실행 설정은 question-worker 모드에서만 받습니다.')
    if args.mode == 'verify-once' and (not args.config or not os.path.isabs(args.config)):
        parser.error('검증 설정의 절대 경로가 필요합니다.')
    if args.mode == 'standby' and args.config:
        parser.error('대기 모드에서는 검증 설정을 받지 않습니다.')
    stop = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
    command = None
    if args.mode == 'verify-once':
        command = [sys.executable, '-m', 'solo_leveling.interfaces.cli.processing_verification',
                   '--config', args.config, '--execute', '--runtime',
                   'solo_leveling.workers.verification_bootstrap:factory']
    if args.mode == 'question-worker':
        command = [sys.executable, '-m', 'solo_leveling.interfaces.cli.question_worker',
                   '--execute', '--runtime', args.runtime, '--max-jobs', str(args.max_jobs)]
    try:
        return supervise(stop, command, preflight=[sys.executable, '/opt/worker/image_preflight.py'])
    finally:
        for sig, handler in previous.items(): signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(main())
