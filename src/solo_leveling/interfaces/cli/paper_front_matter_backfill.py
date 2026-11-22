"""이미 처리된 원격 논문의 빈 제목·저자·출판 연도를 저장된 PDF 첫 페이지에서 채운다.

관리자 명령이다. 번역·임베딩은 다시 하지 않고 PDF를 내려받아 첫 페이지만 읽는다.
기본은 미리보기이며 `--apply`를 줄 때만 DB에 쓴다. 이미 값이 있는 열은 덮어쓰지 않는다.

필요한 환경 변수: DATABASE_URL(server_papers UPDATE 권한), OBJECT_STORAGE_*.
"""

from argparse import ArgumentParser
import json
import logging
from os import environ
from typing import Callable, Mapping, Sequence

from solo_leveling.interfaces.http.settings import load_database_settings, load_object_storage_settings

# 원격 논문마다 가장 최근의 준비된 PDF 한 개만 읽는다. 삭제된 논문은 제외한다.
_CANDIDATES = '''SELECT p.owner_user_id, p.paper_id, u.object_key, u.file_size, u.sha256_hex
    FROM server_papers p
    JOIN LATERAL (
        SELECT object_key, file_size, sha256_hex FROM server_pdf_uploads
        WHERE owner_user_id=p.owner_user_id AND paper_id=p.paper_id AND status='ready'
        ORDER BY updated_at DESC, upload_id ASC LIMIT 1
    ) u ON TRUE
    WHERE p.deleted_at IS NULL AND p.origin='remote'
      AND (p.title IS NULL OR p.authors IS NULL OR p.publication_year IS NULL)
    ORDER BY p.created_at ASC, p.paper_id ASC LIMIT %s'''


def run_backfill(pool, storage, *, limit: int, apply: bool,
                 read_front_matter: Callable[[bytes], object] | None = None,
                 emit: Callable[[dict], None] = lambda item: None) -> dict:
    from solo_leveling.workers.paper_front_matter import fill_missing_paper_metadata
    if read_front_matter is None:
        from solo_leveling.workers.paper_front_matter import read_front_matter
    counts = {'checked': 0, 'updated': 0, 'would_update': 0, 'not_found': 0, 'unchanged': 0, 'failed': 0}
    with pool.connection() as conn:
        rows = conn.execute(_CANDIDATES, (limit,)).fetchall()
    for row in rows:
        counts['checked'] += 1
        item = {'paper_id': str(row['paper_id'])}
        try:
            downloaded = storage.read_object(row['object_key'], expected_size=int(row['file_size']))
            if downloaded.sha256_hex != row['sha256_hex'] or not downloaded.pdf_header_valid:
                raise ValueError('pdf_mismatch')
            found = read_front_matter(downloaded.body)
        except Exception:
            counts['failed'] += 1
            emit({**item, 'status': 'failed'})
            continue
        if found is None:
            counts['not_found'] += 1
            emit({**item, 'status': 'not_found'})
            continue
        item.update(title=found.title, authors=list(found.authors) if found.authors else None,
                    publication_year=found.publication_year)
        try:
            with pool.connection() as conn:
                with conn.transaction():
                    changed = fill_missing_paper_metadata(conn, row['owner_user_id'], str(row['paper_id']), found)
                    if not apply:
                        # 미리보기는 같은 조건으로 실행한 뒤 되돌려 바뀔 행만 확인한다.
                        raise _DryRun(changed)
        except _DryRun as dry:
            changed = dry.changed
        except Exception:
            counts['failed'] += 1
            emit({**item, 'status': 'failed'})
            continue
        status = ('updated' if apply else 'would_update') if changed else 'unchanged'
        counts[status] += 1
        emit({**item, 'status': status})
    return counts


class _DryRun(Exception):
    def __init__(self, changed: bool):
        super().__init__('dry_run')
        self.changed = changed


def _run(source: Mapping[str, str], limit: int, apply: bool, *, pool_factory=None, storage_factory=None,
         emit=print):
    database = load_database_settings(source)
    storage_settings = load_object_storage_settings(source)
    if database is None or storage_settings is None:
        raise ValueError('paper_front_matter_settings_missing')
    if pool_factory is None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        pool_factory = lambda dsn: ConnectionPool(  # noqa: E731 - 지연 import factory
            conninfo=dsn, kwargs={'row_factory': dict_row}, open=True)
    if storage_factory is None:
        from solo_leveling.interfaces.http.object_storage import S3ObjectStorage

        storage_factory = S3ObjectStorage.create
    pool = pool_factory(database.dsn)
    try:
        return run_backfill(pool, storage_factory(storage_settings), limit=limit, apply=apply,
                            emit=lambda item: emit(json.dumps(item, ensure_ascii=False)))
    finally:
        pool.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='찾은 값을 실제로 저장합니다. 없으면 미리보기만 합니다.')
    parser.add_argument('--limit', type=int, default=200, help='한 실행에서 확인할 최대 논문 수(1~1000)')
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 1000:
        print(json.dumps({'status': 'invalid_limit'}))
        return 1
    # 연결 재시도 로그에는 접속 정보가 포함될 수 있어 원시 경고를 막는다.
    pool_logger = logging.getLogger('psycopg.pool')
    suppress_details = lambda record: False  # noqa: E731
    pool_logger.addFilter(suppress_details)
    try:
        result = _run(environ, args.limit, args.apply)
    except Exception:
        print(json.dumps({'status': 'backfill_unavailable'}))
        return 1
    finally:
        pool_logger.removeFilter(suppress_details)
    print(json.dumps({'status': 'complete' if args.apply else 'preview', **result}))
    return 1 if result['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
