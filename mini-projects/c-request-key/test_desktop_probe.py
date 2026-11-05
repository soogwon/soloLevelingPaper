"""요청 복원 실패가 재접수로 이어지지 않는지 임시 DB로 검증한다."""
import importlib.util
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from tests.unit.test_learning_persistence import setup_db, publish


@pytest.fixture
def probe(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['desktop_probe.py', str(tmp_path)])
    spec = importlib.util.spec_from_file_location(
        'desktop_probe_test', Path(__file__).with_name('desktop_probe.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('stored', [
    ['ctx', 17], ['ctx', None], ['ctx', True], [17, str(uuid4())],
    [], ['ctx'], ['ctx', str(uuid4()), 'extra'], 'ctx', 17, None,
    {'ctx': str(uuid4())}, ['ctx', 'bad-uuid'],
])
def test_malformed_journal_never_queries_or_submits(probe, monkeypatch, stored):
    probe.journal.write_text(json.dumps(stored), encoding='utf-8')
    lookup, begin, complete = Mock(), Mock(), Mock()
    monkeypatch.setattr(probe.ask_executions, 'lookup_execution', lookup)
    monkeypatch.setattr(probe.ask_executions, 'begin_execution', begin)
    monkeypatch.setattr(probe.ask_executions, 'complete_execution', complete)
    assert probe.restore() == '키 복원 실패'
    lookup.assert_not_called()
    begin.assert_not_called()
    complete.assert_not_called()


def test_restore_errors_keep_one_execution_and_recovery_still_works(probe, tmp_path, monkeypatch):
    db, chunks = setup_db.__wrapped__(tmp_path)
    publish(db, chunks)
    store = probe.ask_executions
    begin, complete = Mock(wraps=store.begin_execution), Mock(wraps=store.complete_execution)
    monkeypatch.setattr(store, 'begin_execution', begin)
    monkeypatch.setattr(store, 'complete_execution', complete)
    with pytest.raises(RuntimeError, match='합성 접수 응답 유실'):
        probe.submit()
    saved = probe.journal.read_bytes()
    assert begin.call_count == complete.call_count == 1
    begin.reset_mock()
    complete.reset_mock()
    lookup = store.lookup_execution
    monkeypatch.setattr(store, 'lookup_execution',
                        Mock(side_effect=sqlite3.OperationalError('PRIVATE_DB_ERROR')))
    assert probe.restore() == '상태 확인 불가'
    monkeypatch.setattr(store, 'lookup_execution', lookup)
    for content, expected in [
        (b'bad-json', '키 복원 실패'),
        (json.dumps(['ctx', 17]).encode(), '키 복원 실패'),
        (json.dumps(['ctx', str(uuid4())]).encode(), '상태 확인 불가'),
        (saved, '저장된 결과 복원'),
    ]:
        probe.journal.write_bytes(content)
        assert probe.restore() == expected
    probe.journal.unlink()
    assert probe.restore() == '키 복원 실패'
    probe.journal.write_bytes(saved)
    assert probe.submit() == '보관 실패 또는 기존 요청 있음'
    assert probe.restore() == '저장된 결과 복원'
    begin.assert_not_called()
    complete.assert_not_called()
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute('SELECT status FROM ask_executions').fetchall() == [('completed',)]


def test_restore_scopes_lookup_to_saved_context_without_new_execution(probe, tmp_path, monkeypatch):
    db, chunks = setup_db.__wrapped__(tmp_path)
    publish(db, chunks)
    store = probe.ask_executions
    begin = Mock(wraps=store.begin_execution)
    complete = Mock(wraps=store.complete_execution)
    lookup = Mock(wraps=store.lookup_execution)
    monkeypatch.setattr(store, 'begin_execution', begin)
    monkeypatch.setattr(store, 'complete_execution', complete)
    monkeypatch.setattr(store, 'lookup_execution', lookup)
    with pytest.raises(RuntimeError, match='합성 접수 응답 유실'):
        probe.submit()
    begin.reset_mock()
    complete.reset_mock()
    context, key = json.loads(probe.journal.read_text(encoding='utf-8'))

    assert probe.restore() == '저장된 결과 복원'
    lookup.assert_called_once_with(probe.db, key, context_id=probe.context_id)

    assert store.lookup_execution(probe.db, key, context_id='other-context').state.value == 'not_found'
    assert context == probe.context_id
    begin.assert_not_called()
    complete.assert_not_called()


def test_journal_write_failure_never_submits(probe, monkeypatch):
    probe.journal.mkdir()
    begin = Mock()
    monkeypatch.setattr(probe.ask_executions, 'begin_execution', begin)
    assert probe.submit() == '보관 실패 또는 기존 요청 있음'
    begin.assert_not_called()
