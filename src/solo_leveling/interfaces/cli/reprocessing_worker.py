"""승인된 복구 작업 한 건만 실행한다. 기본 차단, 원본 오류와 자격은 출력하지 않는다."""
from argparse import ArgumentParser
from contextlib import redirect_stdout,redirect_stderr
import json
import logging
import os
import signal
from solo_leveling.workers.reprocessing_runtime import factory


def main(argv=None):
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--publish',action='store_true',help='저장 결과를 로컬 임베딩하고 후보 게시본으로 저장')
    args=parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({'status':'execute_required'}));return 2
    # 종료 신호는 후속 전송/저장을 중단한다. 남은 lease는 다음 실행에서 안전하게 판정한다.
    def stop(*_):raise KeyboardInterrupt()
    previous={sig:signal.signal(sig,stop) for sig in (signal.SIGINT,signal.SIGTERM)}
    disabled=logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with open(os.devnull,'w') as sink,redirect_stdout(sink),redirect_stderr(sink):
            with (factory(publish=True) if args.publish else factory()) as worker:
                result=worker.run_once()
                status='published' if args.publish else result
        print(json.dumps({'status':status}))
        return 0 if status in ('staged','idle','published') else 1
    except (Exception,KeyboardInterrupt):
        print(json.dumps({'status':'reprocessing_worker_failed'}));return 1
    finally:
        logging.disable(disabled)
        for sig,handler in previous.items():signal.signal(sig,handler)


if __name__=='__main__':raise SystemExit(main())
