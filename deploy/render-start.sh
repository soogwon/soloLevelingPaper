#!/bin/sh
set -eu

# Render 무료 Web Service는 pre-deploy command를 지원하지 않는다. 스테이징의
# 단일 인스턴스 시작 전에 advisory lock 기반의 멱등 마이그레이션을 실행한다.
solo-leveling-server-migrate

# Free Web Service에는 Shell/One-Off Job이 없다. 명시적으로 켠 staging에서만
# 만료된 원격 업로드의 임시 객체와 DB 상태를 제한된 건수만 정리한다.
# 실패해도 API를 올려 다음 시작에서 멱등 재시도한다.
if [ "${REMOTE_UPLOAD_CLEANUP_ON_START:-false}" = "true" ]; then
    if ! solo-leveling-remote-upload-cleanup \
        --apply \
        --limit "${REMOTE_UPLOAD_CLEANUP_LIMIT:-100}"
    then
        echo '{"event":"remote_upload_cleanup_on_start","status":"failed"}' >&2
    fi
fi

# Free Web Service에는 Shell/One-Off Job이 없다. 명시적으로 켠 staging에서만
# 프로세스 시작 시 삭제 큐를 제한된 건수만 처리한다. 정리 명령이 실패해도
# API를 올려 상태 조회와 다음 시작 시 재시도를 가능하게 한다.
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
