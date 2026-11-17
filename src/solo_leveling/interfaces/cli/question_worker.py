"""운영자가 검토한 질문 runtime을 제한된 횟수만 실행한다. 기본값은 실행하지 않는다."""
from argparse import ArgumentParser
from contextlib import redirect_stdout, redirect_stderr
import importlib
import json
import logging
import os
import re
import signal
from threading import Event

from solo_leveling.workers.remote_question import run_question_queue


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--runtime', help='검토한 서버 조립 함수 module:factory; context manager로 runner 반환')
    parser.add_argument('--max-jobs', type=int, default=1)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({'status': 'execute_required'}))
        return 2
    if (not 1 <= args.max_jobs <= 10000 or not args.runtime
            or not re.fullmatch(r'[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*:[a-zA-Z_]\w*', args.runtime)):
        print(json.dumps({'status': 'question_worker_config_invalid'}))
        return 1
    stop = Event()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    disabled = logging.root.manager.disable
    try:
        # 자격·질문·공급자 원문 오류를 진단으로 내보내지 않는다.
        logging.disable(logging.CRITICAL)
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            module, name = args.runtime.split(':')
            with getattr(importlib.import_module(module), name)() as runner:
                result = run_question_queue(runner, stop, max_jobs=args.max_jobs)
        print(json.dumps({'status': result.status, 'processed': result.processed}))
        return 0 if result.status in ('stopped', 'limit_reached') else 1
    except (Exception, KeyboardInterrupt):
        print(json.dumps({'status': 'question_worker_failed'}))
        return 1
    finally:
        logging.disable(disabled)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(main())
