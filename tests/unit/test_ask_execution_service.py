"""Keyed answer orchestration with the real answer pipeline and a temporary SQLite DB."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Event
from types import SimpleNamespace

import pytest

from solo_leveling.application.evidence_qa.answer import AnswerService
from solo_leveling.application.evidence_qa.entry import ContextSearchResponse
from solo_leveling.domain.ask_execution import ExecutionOutcome as O, ExecutionState as S
from solo_leveling.domain.evidence_qa import SearchResult, SearchScope, RetrievedChunk, RetrievalMethod
from solo_leveling.domain.models import RequestConflictError
from solo_leveling.infrastructure.database import repository
from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.infrastructure.generation import ask_execution_service as service
from solo_leveling.infrastructure.generation.fake_generator import FakeClaimGenerator
from tests.unit.test_learning_persistence import setup_db, publish


@pytest.fixture
def env(setup_db):
    db, chunks = setup_db
    ctx = publish(db, chunks).context_id
    chunks = repository.get_translated_chunks(db, 'p1', 't1')
    entered, release = Event(), Event()
    calls = []

    def search(question, **scope):
        calls.append(scope)
        entered.set()
        assert release.wait(5)
        result = SearchResult(question, SearchScope('v1', 'p1', 't1', scope['pdf_pages']),
                              RetrievalMethod.VECTOR, 'idx', tuple(
            RetrievedChunk(chunk, 1.0, i + 1) for i, chunk in enumerate(chunks)))
        return ContextSearchResponse(ctx, result)

    answer = AnswerService(SimpleNamespace(search=search), FakeClaimGenerator(), None)
    return db, ctx, service.AskExecutionService(db, answer), entered, release, calls


def test_concurrent_request_replays_same_public_result(env):
    db, ctx, runner, entered, release, calls = env
    with ThreadPoolExecutor() as pool:
        first = pool.submit(runner.run, 'key', '질문', context_id=ctx, pdf_pages=(2, 1))
        try:
            assert entered.wait(5)
            waiting = runner.run('key', '질문', context_id=ctx, pdf_pages=(1, 2))
            assert waiting.outcome == O.IN_PROGRESS and waiting.owner_token is None
            assert runner.lookup('key', context_id='other').state == S.NOT_FOUND
            with pytest.raises(RequestConflictError):
                runner.run('key', '질문 ', context_id=ctx, pdf_pages=(1, 2))
        finally:
            release.set()
        saved = first.result()
    assert saved.outcome == O.COMPLETED
    restarted = service.AskExecutionService(db, runner.answer_service)
    assert restarted.lookup('key', context_id=ctx) == saved.view
    assert restarted.run('key', '질문', context_id=ctx, pdf_pages=(1, 2)) == saved
    payload = json.loads(saved.view.result_json)
    assert payload['status'] == 'ok' and len(payload['citations']) == 2
    assert len(calls) == 1 and calls[0]['pdf_pages'] == (1, 2)
    ids = [c['evidence_id'] for c in payload['citations']]
    assert len(repository.get_evidences(db, ctx, ids).evidence) == 2


def test_expired_while_searching_cannot_save(env):
    db, ctx, runner, entered, release, _ = env
    with ThreadPoolExecutor() as pool:
        future = pool.submit(runner.run, 'key', '질문', context_id=ctx)
        try:
            assert entered.wait(5)
            with closing(get_connection(db)) as conn, conn:
                conn.execute("UPDATE ask_executions SET lease_expires_at='2000'")
        finally:
            release.set()
        assert future.result().outcome == O.LEASE_LOST
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM evidences').fetchone()[0] == 0
    assert runner.lookup('key', context_id=ctx).failure_code == 'LEASE_EXPIRED'


def test_result_write_failure_rolls_back_citations(env):
    db, ctx, runner, _, release, calls = env
    release.set()
    with closing(get_connection(db)) as conn, conn:
        conn.execute("""CREATE TRIGGER reject_result BEFORE UPDATE OF result_json ON ask_executions
                        BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END""")
    failed = runner.run('key', '질문', context_id=ctx)
    assert failed.outcome == O.FAILED and failed.view.failure_code == 'ANSWER_FAILED'
    assert runner.run('key', '질문', context_id=ctx) == failed and len(calls) == 1
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM evidences').fetchone()[0] == 0

def test_heartbeat_loss_stops_before_generator(env, monkeypatch):
    db, ctx, runner, entered, release, _ = env
    renewed = Event()
    calls = []
    original = service.store.heartbeat_execution

    def expire(*args):
        with closing(get_connection(db)) as conn, conn:
            conn.execute("UPDATE ask_executions SET lease_expires_at='2000'")
        outcome = original(*args)
        renewed.set()
        return outcome

    monkeypatch.setattr(service, 'HEARTBEAT_SECONDS', 0.05)
    monkeypatch.setattr(service.store, 'heartbeat_execution', expire)
    monkeypatch.setattr(runner.answer_service.generator, 'generate_claims', lambda *args: calls.append(args))
    with ThreadPoolExecutor() as pool:
        work = pool.submit(runner.run, 'slow', '질문', context_id=ctx)
        try:
            assert entered.wait(5) and renewed.wait(5)
        finally:
            release.set()
        assert work.result().outcome == O.LEASE_LOST
    assert not calls


@pytest.mark.parametrize('reject, expected', [(False, 'saved'), (True, 'failed')])
def test_snapshot_reports_committed_history(env, reject, expected):
    from tests.unit.test_verification_history import record
    db, ctx, _, _, _, _ = env
    owner = service.store.begin_execution(db, 'key', 'hash', ctx)
    if reject:
        with closing(get_connection(db)) as conn, conn:
            conn.execute("""CREATE TRIGGER reject_claim BEFORE INSERT ON verification_claims
                            BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END""")
    result = service.store.complete_execution(db, 'key', owner.owner_token, '{}', history=record())
    assert result.outcome == O.SAVED
    assert json.loads(result.view.result_json)['verification_history']['status'] == expected
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM verification_runs').fetchone()[0] == int(not reject)

def test_invalid_scope_does_not_claim_key_and_empty_draft_is_completed(env, monkeypatch):
    from solo_leveling.application.evidence_qa.errors import InvalidArgumentError
    from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft
    _, ctx, runner, _, release, _ = env
    release.set()
    with pytest.raises(InvalidArgumentError):
        runner.run('key', '질문', context_id=ctx, pdf_pages=(1, 1))
    assert runner.lookup('key', context_id=ctx).state == S.NOT_FOUND
    monkeypatch.setattr(runner.answer_service.generator, 'generate_claims', lambda *_: GeneratedAnswerDraft(()))
    done = runner.run('key', '질문', context_id=ctx)
    assert done.outcome == O.COMPLETED
    assert json.loads(done.view.result_json)['status'] == 'insufficient_evidence'

@pytest.mark.parametrize('failure, code', [('renew', 'HEARTBEAT_FAILED'), ('start', 'EXECUTION_START_FAILED')])
def test_worker_failure_is_durable_and_replayed(env, monkeypatch, failure, code):
    db, ctx, runner, entered, release, calls = env
    if failure == 'renew':
        def broken_renew(*args):
            assert entered.wait(5)
            release.set()
            raise sqlite3.OperationalError('synthetic')

        monkeypatch.setattr(service, 'HEARTBEAT_SECONDS', 0.01)
        monkeypatch.setattr(service.store, 'heartbeat_execution', broken_renew)
    else:
        def broken_start(self):
            raise RuntimeError('synthetic')

        monkeypatch.setattr(service.Thread, 'start', broken_start)

    result = runner.run('key', 'question', context_id=ctx)
    assert result.outcome == O.FAILED
    assert result.view.state == S.FAILED and result.view.failure_code == code
    assert runner.lookup('key', context_id=ctx) == result.view
    assert runner.run('key', 'question', context_id=ctx) == result
    assert len(calls) == int(failure == 'renew')
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM evidences').fetchone()[0] == 0


@pytest.mark.parametrize('conflicting, expected', [(False, 'saved'), (True, 'failed')])
def test_duplicate_history_snapshot_matches_stored_content(env, conflicting, expected):
    from solo_leveling.infrastructure.database import verification_history as history
    from tests.unit.test_verification_history import record, claim
    db, ctx, runner, _, _, _ = env
    original = record()
    history.save_verification_history(db, original)
    before = history.get_verification_history(db, ctx, original.run_id)
    assert before is not None
    incoming = record(claims=[claim(text='different')]) if conflicting else original
    owner = service.store.begin_execution(db, 'key', 'hash', ctx)
    result = service.store.complete_execution(db, 'key', owner.owner_token, '{}', history=incoming)
    assert result.outcome == O.SAVED
    assert runner.lookup('key', context_id=ctx) == result.view
    assert json.loads(result.view.result_json)['verification_history']['status'] == expected
    assert history.get_verification_history(db, ctx, original.run_id) == before


def test_double_write_failure_returns_unknown_then_lookup_shows_lease_expiry(env):
    db, ctx, runner, _, release, calls = env
    release.set()
    with closing(get_connection(db)) as conn, conn:
        conn.execute("""CREATE TRIGGER reject_updates BEFORE UPDATE ON ask_executions
                        BEGIN SELECT RAISE(ABORT, 'private storage error'); END""")
    result = runner.run('key', 'question', context_id=ctx)
    assert result == service.ExecutionResult(O.STATUS_UNKNOWN)
    assert runner.lookup('key', context_id=ctx).state == S.RUNNING
    with closing(get_connection(db)) as conn, conn:
        assert conn.execute('SELECT COUNT(*) FROM evidences').fetchone()[0] == 0
        conn.execute('DROP TRIGGER reject_updates')
        conn.execute("UPDATE ask_executions SET lease_expires_at='2000' WHERE request_key='key'")
    expired = runner.lookup('key', context_id=ctx)
    assert expired.state == S.FAILED and expired.failure_code == 'LEASE_EXPIRED'
    assert runner.run('key', 'question', context_id=ctx).view == expired
    assert len(calls) == 1


def test_missing_row_during_heartbeat_returns_not_found(env, monkeypatch):
    db, ctx, runner, entered, release, _ = env
    original = service.store.heartbeat_execution

    def remove_then_renew(*args):
        assert entered.wait(5)
        with closing(get_connection(db)) as conn, conn:
            conn.execute("DELETE FROM ask_executions WHERE request_key='key'")
        try:
            return original(*args)
        finally:
            release.set()

    monkeypatch.setattr(service, 'HEARTBEAT_SECONDS', 0.01)
    monkeypatch.setattr(service.store, 'heartbeat_execution', remove_then_renew)
    assert runner.run('key', 'question', context_id=ctx).outcome == O.NOT_FOUND
    assert runner.lookup('key', context_id=ctx).state == S.NOT_FOUND
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM evidences').fetchone()[0] == 0
