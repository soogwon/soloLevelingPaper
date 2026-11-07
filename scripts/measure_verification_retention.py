"""#82: 기존 이력의 복사본에서만 비용·수동 정리·공간 회수를 측정한다. API 호출 없음."""

import argparse
from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import platform
from pathlib import Path
import sqlite3
import statistics
import sys
from time import perf_counter
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from solo_leveling.infrastructure.database import verification_history as history
from solo_leveling.infrastructure.database import verification_retention as retention
from solo_leveling.infrastructure.database.schema import init_db
from solo_leveling.domain.verification_history import HistoryOutcome
from solo_leveling.domain.verification_retention import RetentionPolicy, CleanupOutcome

HISTORY = ('verification_runs', 'verification_claims', 'verification_claim_refs')
MAX_MEASURED_RUNS = 10000


def require(condition, code):
    if not condition:
        raise ValueError(code)


def source_fingerprint(source):
    # 활성/미체크포인트 DB는 대상이 아니다. SQLite로 원본을 열어 SHM을 갱신하지 않는다.
    if any(Path(str(source)+suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('SOURCE_SIDECAR_PRESENT')
    before = source.stat()
    digest = sha256()
    with source.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    after = source.stat()
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    require(signature(before) == signature(after), 'SOURCE_CHANGED')
    require(not any(Path(str(source)+s).exists() for s in ('-wal', '-shm', '-journal')), 'SOURCE_CHANGED')
    return signature(after), digest.hexdigest()


def copy_offline_source(source, destination):
    expected = source_fingerprint(source)
    digest = sha256()
    with source.open('rb') as src, destination.open('xb') as dst:
        for block in iter(lambda: src.read(1024*1024), b''):
            digest.update(block)
            dst.write(block)
    require(digest.hexdigest() == expected[1] and source_fingerprint(source) == expected, 'SOURCE_CHANGED')
    return expected


def timed(call):
    start = perf_counter()
    result = call()
    return result, round((perf_counter()-start)*1000, 4)


def percentiles(values):
    values = sorted(values)
    return dict(count=len(values), median_ms=statistics.median(values),
                p95_ms=values[max(0, (95*len(values)+99)//100-1)], max_ms=max(values))


def protected_digest(db, *, include_history=False):
    """테이블 내용의 해시만 반환한다. VACUUM에서 바뀔 수 있는 rowid는 포함하지 않는다."""
    with closing(sqlite3.connect(db)) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if (include_history or r[0] not in HISTORY) and not r[0].startswith('sqlite_')]
        result = {}
        for table in tables:
            name = table.replace('"', '""')
            rows = sorted(json.dumps(row, ensure_ascii=True, default=lambda b: {'blob_hex': b.hex()})
                          for row in conn.execute(f'SELECT * FROM "{name}"'))
            result[table] = dict(rows=len(rows), sha256=sha256('\n'.join(rows).encode()).hexdigest())
        return result


def sizes(db):
    with closing(sqlite3.connect(db)) as conn:
        page_size = conn.execute('PRAGMA page_size').fetchone()[0]
        logical = page_size*conn.execute('PRAGMA page_count').fetchone()[0]
        free = page_size*conn.execute('PRAGMA freelist_count').fetchone()[0]
        try:
            allocations = {r[0]: r[1] for r in conn.execute(
                'SELECT name,SUM(pgsize) FROM dbstat GROUP BY name')}
        except sqlite3.OperationalError:
            allocations = None
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' "
                 "AND tbl_name IN ('verification_runs','verification_claims','verification_claim_refs')")]
        history_tables = sum(allocations.get(t, 0) for t in HISTORY) if allocations is not None else None
        history_indexes = sum(allocations.get(t, 0) for t in names) if allocations is not None else None
    wal = Path(str(db)+'-wal')
    return dict(main_bytes=Path(db).stat().st_size, wal_bytes=wal.stat().st_size if wal.exists() else 0,
                logical_bytes=logical, free_bytes=free,
                history_table_bytes=history_tables, history_index_bytes=history_indexes)


def measure(source, output, copies=100):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file() or type(copies) is not int or not 1 <= copies <= 10000:
        raise ValueError('INVALID_INPUT')
    # 기존 디렉터리는 재사용하지 않는다. 복사 대상 혼동과 덮어쓰기를 방지한다.
    output.mkdir(parents=True, exist_ok=False)
    db = output/'measurement.sqlite3'
    # 원본은 파일 읽기만 한다. 운영 앱이 닫힌 오프라인 사본을 사용해야 한다.
    source_before = copy_offline_source(source, db)
    init_db(str(db))  # 최신 스키마 이관은 복사본에만 적용한다.
    protected = protected_digest(db)
    with closing(sqlite3.connect(db)) as conn:
        sample_count = conn.execute('SELECT COUNT(*) FROM verification_runs').fetchone()[0]
        require(sample_count*copies <= MAX_MEASURED_RUNS, 'MEASUREMENT_LIMIT')
        keys = conn.execute('SELECT context_id,run_id FROM verification_runs ORDER BY created_at,run_id').fetchall()
        settings = dict(journal_mode=conn.execute('PRAGMA journal_mode').fetchone()[0],
                        page_size=conn.execute('PRAGMA page_size').fetchone()[0],
                        synchronous=conn.execute('PRAGMA synchronous').fetchone()[0])
    samples = [history.get_verification_history(str(db), ctx, run) for ctx, run in keys]
    if not samples:
        raise ValueError('NO_SAMPLES')
    sample_stats = dict(runs=len(samples), claims=sum(len(s.claims) for s in samples),
        references=sum(len(c.references) for s in samples for c in s.claims),
        failures=sum(s.failure is not None for s in samples),
        claim_text_utf8_bytes=sum(len(c.text.encode()) for s in samples for c in s.claims))
    original_sizes = sizes(db)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('PRAGMA foreign_keys=ON')
        for table in reversed(HISTORY):
            conn.execute(f'DELETE FROM {table}')
    reclaimed = retention.reclaim_space(str(db))
    if reclaimed.outcome.value != 'completed':
        raise ValueError('BASELINE_RECLAIM_FAILED')
    baseline = sizes(db)
    writes, reads, points, ids = [], [], [], []
    for i in range(copies):
        for sample in samples:
            record = replace(sample, run_id=uuid4().hex, created_at=None)
            result, elapsed = timed(lambda: history.save_verification_history(str(db), record))
            if result.outcome is not HistoryOutcome.SAVED:
                raise ValueError('SAMPLE_SAVE_FAILED')
            writes.append(elapsed)
            restored, elapsed = timed(lambda: history.get_verification_history(str(db), record.context_id, record.run_id))
            if restored is None or replace(restored, created_at=None) != record:
                raise ValueError('SAMPLE_READ_MISMATCH')
            reads.append(elapsed)
            ids.append(record.run_id)
        if i in {0, copies//2, copies-1}:
            points.append(dict(runs=len(ids), **sizes(db)))
    list_reads = []
    context_id = samples[0].context_id
    for _ in range(5):
        listed, elapsed = timed(lambda: history.list_verification_history(str(db), context_id))
        if len(listed) != copies*sum(s.context_id == context_id for s in samples):
            raise ValueError('LIST_COUNT_MISMATCH')
        list_reads.append(elapsed)
    now = datetime.now(timezone.utc)
    # 90일 경계와 최근 기록은 보존하고 나머지 복제본만 91일 전으로 설정한다.
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('UPDATE verification_runs SET created_at=?', ((now-timedelta(days=91)).isoformat(),))
        keep = ids[-2:] if len(ids)>1 else ids
        for offset, run_id in enumerate(keep):
            conn.execute('UPDATE verification_runs SET created_at=? WHERE run_id=?',
                         ((now-timedelta(days=90 if offset==0 else 1)).isoformat(), run_id))
    before_plan = protected_digest(db, include_history=True), sizes(db)
    with closing(sqlite3.connect(db)) as conn:
        kept_contexts = conn.execute('SELECT context_id,run_id FROM verification_runs WHERE run_id IN ('
                                    + ','.join('?' for _ in keep) + ')', keep).fetchall()
    kept_records = [history.get_verification_history(str(db), ctx, run) for ctx, run in kept_contexts]
    policy = RetentionPolicy(90, 200)
    plan, plan_ms = timed(lambda: retention.plan_verification_cleanup(str(db), policy, now))
    require(before_plan == (protected_digest(db, include_history=True), sizes(db)), 'PLAN_CHANGED_DATA')
    backup = output/'before-cleanup.sqlite3'
    _, backup_ms = timed(lambda: retention.backup_database(str(db), str(backup)))
    require(protected_digest(backup, include_history=True) == before_plan[0], 'BACKUP_CONTENT_MISMATCH')
    with closing(sqlite3.connect(backup)) as conn:
        require(conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'BACKUP_INTEGRITY_FAILED')
    cleanup, cleanup_ms = timed(lambda: retention.run_verification_cleanup(str(db), policy, now))
    require(cleanup.outcome is CleanupOutcome.COMPLETED
            and (cleanup.deleted_runs, cleanup.deleted_claims, cleanup.deleted_refs)
                == (plan.eligible_runs, plan.eligible_claims, plan.eligible_refs)
            and cleanup.deleted_runs == len(ids)-len(keep),
            'CLEANUP_COUNT_MISMATCH')
    after_delete = sizes(db)
    remaining = []
    with closing(sqlite3.connect(db)) as conn:
        remaining = [r[0] for r in conn.execute('SELECT run_id FROM verification_runs')]
    require(set(remaining) == set(keep), 'RETAINED_RUNS_MISMATCH')
    require(kept_records == [history.get_verification_history(str(db), ctx, run)
                             for ctx, run in kept_contexts], 'RETAINED_CONTENT_CHANGED')
    require(protected_digest(db) == protected and retention.count_foreign_key_violations(str(db)) == 0,
            'PROTECTED_DATA_CHANGED')
    before_reclaim = protected_digest(db, include_history=True)
    reclaim, reclaim_ms = timed(lambda: retention.reclaim_space(str(db)))
    require(reclaim.outcome.value == 'completed'
            and protected_digest(db, include_history=True) == before_reclaim
            and retention.count_foreign_key_violations(str(db)) == 0,
            'RECLAIM_FAILED')
    require(source_fingerprint(source) == source_before, 'SOURCE_CHANGED')
    report = dict(sample=sample_stats, replication_copies=copies, measured_runs=len(ids),
        platform=platform.system(), python_version=platform.python_version(),
        sqlite_settings=settings, source_files_unchanged=True,
        sqlite_version=sqlite3.sqlite_version, sample_copy_sizes=original_sizes, empty_baseline=baseline,
        growth=points, save=percentiles(writes), get=percentiles(reads),
        list_context=dict(rows=len(listed), **percentiles(list_reads)),
        plan=dict(**asdict(plan), elapsed_ms=plan_ms), backup_ms=backup_ms,
        cleanup=dict(deleted_runs=cleanup.deleted_runs, elapsed_ms=cleanup_ms), after_delete=after_delete,
        reclaim=dict(outcome=reclaim.outcome.value, elapsed_ms=reclaim_ms, **sizes(db)),
        protected_tables_unchanged=True, foreign_key_violations=0, backup_verified=True)
    with (output/'report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-db', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--copies', type=int, default=100)
    args = parser.parse_args()
    try:
        report = measure(args.source_db, args.output_dir, args.copies)
    except Exception:
        # 오류 본문·경로·표본을 로그에 출력하지 않는다.
        print('result=failed', file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(report, ensure_ascii=True))
