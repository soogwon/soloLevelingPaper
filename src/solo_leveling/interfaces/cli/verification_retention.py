"""검증 이력 보존·정리 명령(#82). 자동 실행·예약은 없고, 사람이 명령을 직접 실행할 때만 동작한다.

  plan    정리 대상 건수·기간만 보여 준다(데이터 변경 없음).
  run     보존 기간이 지난 검증 이력만 지운다. --yes가 있어야 실행된다.
  reclaim 행 삭제로 비어 있는 파일 공간을 회수한다(VACUUM). --yes가 있어야 실행된다.

보존 기간(--older-than-days)은 기본값이 없다. 출력에는 건수·기간·결과 코드만 쓴다.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

from solo_leveling.domain.verification_retention import (
    CleanupOutcome, RetentionPolicy, RetentionValidationError, SpaceOutcome, DEFAULT_BATCH_SIZE)
from solo_leveling.infrastructure.database import verification_retention as store


def _out(**fields):
    print(' '.join(f'{key}={value}' for key, value in fields.items()))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='검증 이력 보존·정리')
    parser.add_argument('command', choices=('plan', 'run', 'reclaim'))
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--older-than-days', type=int, help='이 일수보다 오래된 검증 이력만 대상. 기본값 없음')
    parser.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument('--yes', action='store_true', help='실제로 변경한다(run·reclaim에 필요)')
    parser.add_argument('--backup-to', type=Path, help='변경 전에 이 경로로 DB 백업. 이미 있으면 중단')
    args = parser.parse_args(argv)
    db = str(args.db)
    if not args.db.is_file():
        _out(result='db_not_found')
        return 2
    if args.command in ('run', 'reclaim') and not args.yes:
        _out(result='confirmation_required')
        return 2
    try:
        if args.command in ('plan', 'run'):
            if args.older_than_days is None:
                _out(result='retention_required')
                return 2
            policy = RetentionPolicy(args.older_than_days, args.batch_size)
        if args.backup_to is not None and args.command != 'plan':
            store.backup_database(db, str(args.backup_to))
            _out(backup='created')
        if args.command == 'plan':
            plan = store.plan_verification_cleanup(db, policy)
            _out(mode='dry-run', cutoff=plan.cutoff, total_runs=plan.total_runs, eligible_runs=plan.eligible_runs,
                 eligible_claims=plan.eligible_claims, eligible_refs=plan.eligible_refs,
                 oldest=plan.oldest_created_at, newest_eligible=plan.newest_eligible_created_at)
            return 0
        if args.command == 'run':
            result = store.run_verification_cleanup(db, policy)
            _out(result=result.outcome.value, deleted_runs=result.deleted_runs, deleted_claims=result.deleted_claims,
                 deleted_refs=result.deleted_refs, batches=result.batches,
                 fk_violations=store.count_foreign_key_violations(db),
                 reclaimable_bytes=store.space_report(db).free_bytes_in_file)
            return 0 if result.outcome is CleanupOutcome.COMPLETED else 1
        space = store.reclaim_space(db)
        _out(result=space.outcome.value, before_bytes=space.before_bytes, after_bytes=space.after_bytes)
        return 0 if space.outcome is SpaceOutcome.COMPLETED else 1
    except RetentionValidationError as error:
        _out(result='invalid_setting', field=error)
        return 2
    except FileExistsError:
        _out(result='backup_exists')
        return 2
    except sqlite3.Error as error:
        # 원본 예외 문구·경로는 출력하지 않는다. 실제 잠금만 재시도할 수 있는 상황(locked)이고 나머지는 failed다.
        _out(result='locked' if store.is_lock_error(error) else 'failed')
        return 1
    except OSError:
        _out(result='failed')
        return 1


if __name__ == '__main__':
    sys.exit(main())
