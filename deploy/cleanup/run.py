"""별도 정리 실행의 계획과 승인된 단발 실행을 분리한다."""

import argparse
import json
import os
import subprocess


COMMANDS = {
    "uploads": ["solo-leveling-remote-upload-cleanup", "--apply", "--limit", "100"],
    "deletions": ["solo-leveling-remote-deletion-cleanup", "--apply", "--max-jobs", "10", "--lease-seconds", "300"],
}


def main(argv=None, *, source=None, runner=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=tuple(COMMANDS))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    # 계획 확인은 DB/R2 자격을 읽거나 자식 명령을 실행하지 않는다.
    if not args.apply:
        print(json.dumps({"status": "plan_only", "target": args.target,
                          "command": COMMANDS[args.target], "timeout_seconds": 600}))
        return 0
    values = os.environ if source is None else source
    if values.get("APP_ENV") != "staging" or values.get("SLP_CLEANUP_APPROVED") != "true":
        print(json.dumps({"status": "approval_required"}))
        return 2
    # 처리 worker나 웹의 자격을 묵시적으로 재사용하지 않는다.
    dsn = values.get("SLP_CLEANUP_DATABASE_URL", "")
    if not dsn or not all(values.get(k) for k in (
        "OBJECT_STORAGE_ENDPOINT", "OBJECT_STORAGE_BUCKET", "OBJECT_STORAGE_ACCESS_KEY_ID",
        "OBJECT_STORAGE_SECRET_ACCESS_KEY",
    )):
        print(json.dumps({"status": "settings_required"}))
        return 2
    env = dict(values)
    env["DATABASE_URL"] = dsn
    run = subprocess.run if runner is None else runner
    try:
        # 실패 원문이나 연결 자격은 상위 서비스 로그에 전달하지 않는다.
        for command in (["solo-leveling-server-migrate", "--check"], COMMANDS[args.target]):
            result = run(command, env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, timeout=600, check=False)
            if result.returncode:
                print(json.dumps({"status": "cleanup_failed", "target": args.target}))
                return 1
    except (OSError, subprocess.SubprocessError):
        print(json.dumps({"status": "cleanup_unavailable", "target": args.target}))
        return 1
    print(json.dumps({"status": "complete", "target": args.target}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
