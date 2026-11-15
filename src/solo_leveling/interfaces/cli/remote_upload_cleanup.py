"""만료된 원격 PDF 임시 객체를 명시적으로 순환 정리한다."""

from argparse import ArgumentParser
import json
import logging
from os import environ
from typing import Mapping, Sequence

from solo_leveling.interfaces.http.remote_uploads import (
    PostgresRemoteUploadIntentStore,
    RemoteUploadVerificationService,
)
from solo_leveling.interfaces.http.settings import (
    load_database_settings,
    load_object_storage_settings,
)


def _run(source: Mapping[str, str], limit: int, *, pool_factory=None, storage_factory=None):
    database = load_database_settings(source)
    storage_settings = load_object_storage_settings(source)
    if database is None or storage_settings is None:
        raise ValueError("remote_upload_cleanup_settings_missing")
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
        service = RemoteUploadVerificationService(
            PostgresRemoteUploadIntentStore(pool), storage_factory(storage_settings))
        return service.cleanup_expired(limit=limit)
    finally:
        pool.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="실제 임시 객체 정리를 수행합니다.")
    parser.add_argument("--limit", type=int, default=100, help="한 실행에서 처리할 최대 개수(1~1000)")
    args = parser.parse_args(argv)
    if not args.apply:
        print(json.dumps({"status": "apply_required"}))
        return 2
    if not 1 <= args.limit <= 1000:
        print(json.dumps({"status": "invalid_limit"}))
        return 1
    # 독립 관리 명령의 연결 재시도 로그에는 접속 정보가 포함될 수 있다.
    # 풀 종료까지 원시 경고를 차단하고 최종 고정 상태만 출력한다.
    pool_logger = logging.getLogger("psycopg.pool")
    suppress_details = lambda record: False  # noqa: E731
    pool_logger.addFilter(suppress_details)
    try:
        result = _run(environ, args.limit)
    except Exception:
        print(json.dumps({"status": "cleanup_unavailable"}))
        return 1
    finally:
        pool_logger.removeFilter(suppress_details)
    print(json.dumps({"status": "complete", **result}))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
