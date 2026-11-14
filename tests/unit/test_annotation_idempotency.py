"""생성 요청 재전송·동시 실행·실패와 삭제 뒤 재시도를 실제 SQLite로 검증한다."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import threading

import pytest

from solo_leveling.application.annotations.service import AnnotationService
from solo_leveling.domain.annotation import AnnotationOutcome, AnnotationValidationError
from solo_leveling.domain.models import LearningContext
from solo_leveling.infrastructure.database import annotations as ann
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection, init_db
from tests.unit.test_annotations import env, seg, counts
from tests.unit.test_learning_persistence import setup_db


def test_restart_replay_current_record_and_deleted_tombstone(env):
    db, ctx = env
    first = AnnotationService(db).create(ctx, 'note', '메모', request_key='retry-1')
    init_db(db)
    replay = AnnotationService(db).create(ctx, 'note', '메모', request_key='retry-1')
    assert replay.reused and replay.annotation == first.annotation and not first.reused
    a = first.annotation
    updated = ann.update_annotation(db, ctx, a.annotation_id, 1, '수정한 메모')
    assert AnnotationService(db).create(ctx, 'note', '메모', request_key='retry-1').annotation == updated.annotation
    ann.delete_annotation(db, ctx, a.annotation_id, 2)
    retry = AnnotationService(db).create(ctx, 'note', '메모', request_key='retry-1')
    assert retry.code == 'ANNOTATION_DELETED'
    assert counts(db) == (0, 0)


def test_concurrent_create_is_one_record(env):
    db, ctx = env
    barrier = threading.Barrier(4)
    def create(_):
        barrier.wait()
        return AnnotationService(db).create(ctx, 'note', '동시 요청', segments=[seg()], request_key='same')
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, range(4)))
    assert all(result.annotation == results[0].annotation for result in results)
    assert sum(result.reused for result in results) == 3
    assert counts(db) == (1, 1)


@pytest.mark.parametrize('changes', [
    {'body': '다른 메모'}, {'step': 'review'}, {'segments': [seg()]},
    {'body': '메모 '},
])
def test_key_conflict_preserves_original(env, changes):
    db, ctx = env
    args = dict(context_id=ctx, kind='note', body='메모', request_key='same')
    first = ann.create_annotation(db, **args)
    conflict = ann.create_annotation(db, **(args | changes))
    assert conflict.code == 'REQUEST_KEY_CONFLICT'
    assert ann.get_annotation(db, ctx, first.annotation.annotation_id) == first.annotation
    assert counts(db) == (1, 0)


def test_invalid_reference_does_not_consume_key(env):
    db, ctx = env
    failed = ann.create_annotation(db, ctx, 'note', '메모', [seg(quote='Other')], request_key='retry')
    assert failed.code == 'QUOTE_MISMATCH'
    success = ann.create_annotation(db, ctx, 'note', '메모', [seg()], request_key='retry')
    assert success.outcome == AnnotationOutcome.SAVED
    assert counts(db) == (1, 1)


def test_transaction_rolls_back_if_request_insert_fails(env):
    db, ctx = env
    with closing(get_connection(db)) as conn, conn:
        conn.execute("CREATE TRIGGER reject_request BEFORE INSERT ON annotation_create_requests "
                     "BEGIN SELECT RAISE(ABORT, 'test'); END")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        ann.create_annotation(db, ctx, 'note', '메모', [seg()], request_key='retry')
    assert counts(db) == (0, 0)
    with closing(get_connection(db)) as conn, conn:
        conn.execute('DROP TRIGGER reject_request')
    assert ann.create_annotation(db, ctx, 'note', '메모', request_key='retry').annotation


@pytest.mark.parametrize('key', ['', ' ', 'x'*129, True, 1, '한글', 'a\n'])
def test_invalid_request_key_saves_nothing(env, key):
    db, ctx = env
    with pytest.raises(AnnotationValidationError) as error:
        ann.create_annotation(db, ctx, 'note', '메모', request_key=key)
    assert error.value.code == 'INVALID_REQUEST_KEY'
    assert counts(db) == (0, 0)


def test_unkeyed_calls_keep_existing_behavior(env):
    db, ctx = env
    a = ann.create_annotation(db, ctx, 'note', '메모')
    b = ann.create_annotation(db, ctx, 'note', '메모')
    assert a.annotation.annotation_id != b.annotation.annotation_id


def test_keys_are_scoped_by_context_and_failed_context_does_not_reserve(env):
    db, ctx = env
    assert ann.create_annotation(db, 'other', 'note', '메모', request_key='key').code == 'CONTEXT_NOT_FOUND'
    repo.create_learning_context(db, LearningContext('other', 'v1', 'p1', 't1', embedding_set_id='idx'))
    a = ann.create_annotation(db, ctx, 'note', '메모', request_key='key')
    b = ann.create_annotation(db, 'other', 'note', '다른 메모', request_key='key')
    assert a.annotation.annotation_id != b.annotation.annotation_id
    assert counts(db) == (2, 0)


def test_upgrade_adds_request_table_without_changing_existing_records(env):
    db, ctx = env
    existing = ann.create_annotation(db, ctx, 'note', '이전 기록')
    with closing(get_connection(db)) as conn, conn:
        conn.execute('DROP TABLE annotation_create_requests')
    init_db(db)
    init_db(db)
    assert ann.get_annotation(db, ctx, existing.annotation.annotation_id) == existing.annotation
    assert ann.create_annotation(db, ctx, 'note', '새 기록', request_key='key').annotation
    with closing(get_connection(db)) as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        row = conn.execute('SELECT * FROM annotation_create_requests').fetchone()
        assert row['input_hash'] and '새 기록' not in tuple(row)
