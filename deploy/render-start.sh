#!/bin/sh
set -eu

# Render 무료 Web Service는 pre-deploy command를 지원하지 않는다. 스테이징의
# 단일 인스턴스 시작 전에 advisory lock 기반의 멱등 마이그레이션을 실행한다.
solo-leveling-server-migrate

# Free Web Service에는 Shell/One-Off Job이 없다. 명시적으로 켠 staging에서만
# 배포 시작 시 bounded 삭제 큐를 한 번 비운다. 정리 실패는 안전 상태(failed)를
# 남기므로 API를 올려 상태 조회와 다음 배포 재시도를 가능하게 한다.
if [ "${REMOTE_DELETION_CLEANUP_ON_START:-false}" = "true" ]; then
    if ! solo-leveling-remote-deletion-cleanup \
        --apply \
        --max-jobs "${REMOTE_DELETION_CLEANUP_MAX_JOBS:-10}" \
        --lease-seconds "${REMOTE_DELETION_CLEANUP_LEASE_SECONDS:-300}"
    then
        echo '{"event":"remote_deletion_cleanup_on_start","status":"failed"}' >&2
    fi
fi

exec solo-leveling-api
