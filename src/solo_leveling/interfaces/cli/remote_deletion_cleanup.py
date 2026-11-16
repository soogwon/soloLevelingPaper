"""접수된 서버 논문 삭제를 PostgreSQL과 객체 저장소에서 순환 정리한다."""

from argparse import ArgumentParser
import json
import logging
from os import environ
from typing import Mapping, Sequence
from uuid import uuid4

from solo_leveling.infrastructure.storage.remote_deletion_backend import (
    create_remote_deletion_cleanup,
)
from solo_leveling.interfaces.http.settings import (
    load_database_settings,
    load_object_storage_settings,
)


def _run(
    source: Mapping[str, str],
    max_jobs: int,
    lease_seconds: int,
    *,
    pool_factory=None,
    storage_factory=None,
):
    database = load_database_settings(source)
    storage_settings = load_object_storage_settings(source)
    if database is None or storage_settings is None:
        raise ValueError("remote_deletion_cleanup_settings_missing")
    if pool_factory is None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        pool_factory = lambda dsn: ConnectionPool(  # noqa: E731 - 지연 import factory
            conninfo=dsn, kwargs={"row_factory": dict_row}, open=True)
    if storage_factory is None:
        from solo_leveling.interfaces.http.object_storage import S3ObjectStorage

        storage_factory = S3ObjectStorage.create

    pool = pool_factory(database.dsn)
    try:
        service = create_remote_deletion_cleanup(
            pool,
            storage_factory(storage_settings),
            worker_id=f"deletion-{uuid4()}",
            lease_seconds=lease_seconds,
        )
        completed = 0
        for _ in range(max_jobs):
            result = service.run_once()
            if result["status"] == "idle":
                return {"processed": completed, "completed": completed, "failed": 0, "idle": True}
            if result["status"] == "failed":
                # failed 요청은 즉시 다시 claim할 수 있다. 한 실행에서 같은 장애를 반복하지 않는다.
                return {"processed": completed + 1, "completed": completed, "failed": 1, "idle": False}
            if result["status"] != "complete":
                raise RuntimeError("remote_deletion_cleanup_result_invalid")
            completed += 1
        return {"processed": completed, "completed": completed, "failed": 0, "idle": False}
    finally:
        pool.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="실제 DB와 객체 저장소 정리를 수행합니다.")
    parser.add_argument("--max-jobs", type=int, default=100, help="한 실행에서 처리할 최대 요청 수(1~1000)")
    parser.add_argument("--lease-seconds", type=int, default=300, help="삭제 단계 lease 시간(30~3600초)")
    args = parser.parse_args(argv)
    if not args.apply:
        print(json.dumps({"status": "apply_required"}))
        return 2
    if not 1 <= args.max_jobs <= 1000 or not 30 <= args.lease_seconds <= 3600:
        print(json.dumps({"status": "invalid_limits"}))
        return 1

    # 연결 실패 로그에는 DSN이나 endpoint가 포함될 수 있으므로 고정 결과만 출력한다.
    pool_logger = logging.getLogger("psycopg.pool")
    suppress_details = lambda record: False  # noqa: E731
    pool_logger.addFilter(suppress_details)
    try:
        result = _run(environ, args.max_jobs, args.lease_seconds)
    except Exception:
        print(json.dumps({"status": "cleanup_unavailable"}))
        return 1
    finally:
        pool_logger.removeFilter(suppress_details)
    print(json.dumps({"status": "complete", **result}))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
