"""승인된 퀴즈 큐를 실행한다. 실행 플래그가 없으면 DB·API에 접근하지 않는다."""
from argparse import ArgumentParser
from contextlib import redirect_stdout, redirect_stderr
import json
import logging
import os
import signal
from threading import Event

from solo_leveling.workers.quiz_runtime import factory
from solo_leveling.workers.processing_queue import QueueRunResult


def run_queue(runner, stop, *, max_jobs, idle_seconds=5):
    if max_jobs is not None and (type(max_jobs) is not int or not 1 <= max_jobs <= 10000):
        raise ValueError('quiz_worker_limit_invalid')
    processed = 0
    while max_jobs is None or processed < max_jobs:
        if stop.is_set():
            return QueueRunResult('stopped', processed)
        status = runner.run_once()
        if status is None:
            if stop.wait(idle_seconds):
                return QueueRunResult('stopped', processed)
            continue
        processed += 1
        if status not in ('finished','expired','blocked','cancelled'):
            return QueueRunResult('failed', processed)
    return QueueRunResult('limit_reached', processed)


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    lifetime = parser.add_mutually_exclusive_group()
    lifetime.add_argument('--max-jobs', type=int, default=None)
    lifetime.add_argument('--continuous', action='store_true')
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({'status':'execute_required'}))
        return 2
    count = None if args.continuous else (1 if args.max_jobs is None else args.max_jobs)
    if count is not None and not 1 <= count <= 10000:
        print(json.dumps({'status':'quiz_worker_config_invalid'}))
        return 1
    stop = Event()
    previous = {sig:signal.signal(sig,lambda *_:stop.set()) for sig in (signal.SIGINT,signal.SIGTERM)}
    disabled = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with open(os.devnull,'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            with factory() as runner:
                result = run_queue(runner, stop, max_jobs=count)
        print(json.dumps({'status':result.status,'processed':result.processed}))
        return 0 if result.status in ('stopped','limit_reached') else 1
    except (Exception,KeyboardInterrupt):
        print(json.dumps({'status':'quiz_worker_failed'}))
        return 1
    finally:
        logging.disable(disabled)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(main())
