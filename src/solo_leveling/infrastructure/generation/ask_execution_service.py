"""Run keyed answers and replay their public response (#119)."""

from copy import copy
import hashlib
import json
import sqlite3
from threading import Event, Thread

from solo_leveling.application.evidence_qa.answer import AnswerGenerationError
from solo_leveling.application.evidence_qa.errors import InvalidArgumentError
from solo_leveling.application.evidence_qa.serialization import public_answer_payload
from solo_leveling.domain.ask_execution import (
    ExecutionOutcome as Outcome, ExecutionResult, ExecutionState, ExecutionView,
    HEARTBEAT_SECONDS, check_text,
)
from solo_leveling.application.evidence_qa.entry import validate_search_input
from solo_leveling.infrastructure.database import ask_executions as store


class _HeartbeatStopped(RuntimeError):
    pass


class _PendingWrites:
    """Evidence and optional verification history from one answer, held until commit."""
    def __init__(self, context_id):
        self.context_id = context_id
        self.evidences = ()
        self.history = None

    def save(self, context_id, evidences):
        if context_id != self.context_id:
            raise ValueError('context mismatch')
        self.evidences = tuple(evidences)

    def save_history(self, record):
        self.history = record
        return True  # The transaction sets the final public status.


class AskExecutionService:
    def __init__(self, db_path, answer):
        self.db_path = db_path
        self.answer_service = answer

    def lookup(self, request_key, *, context_id):
        check_text(context_id, 'context_id')
        view = store.lookup_execution(self.db_path, request_key)
        if view.context_id != context_id:
            return ExecutionView(ExecutionState.NOT_FOUND)
        return view

    def run(self, request_key, question, *, context_id, top_k=5, pdf_pages=(), section_ids=(), strict_pages=False):
        """Use the effective question. Return a token-free result for both first call and replay."""
        try:
            validate_search_input(question, top_k, pdf_pages, section_ids)
            if type(strict_pages) is not bool:
                raise ValueError('strict_pages must be bool')
            if top_k > 20:
                raise ValueError('top_k exceeds 20')
        except ValueError as error:
            raise InvalidArgumentError(str(error)) from None
        scope = dict(context_id=context_id, top_k=top_k, pdf_pages=tuple(sorted(pdf_pages)),
                     section_ids=tuple(sorted(section_ids)))
        # False는 기존 기본 동작과 동일하다. 기존 v1 키를 재실행하지 않도록 해시를 보존한다.
        if strict_pages:
            scope['strict_pages'] = True
        data = json.dumps(dict(question=question, **scope), sort_keys=True, separators=(',', ':'))
        digest = 'v1:' + hashlib.sha256(data.encode('ascii')).hexdigest()
        started = store.begin_execution(self.db_path, request_key, digest, context_id)
        if started.outcome != Outcome.STARTED:
            return started
        args = (self.db_path, request_key, started.owner_token)
        stopped, heartbeat_interrupted = Event(), Event()
        terminal_result = None

        def renew():
            nonlocal terminal_result
            while not stopped.wait(HEARTBEAT_SECONDS):
                try:
                    result = store.heartbeat_execution(*args)
                except Exception:
                    heartbeat_interrupted.set()
                    return
                if result.outcome != Outcome.RENEWED:
                    terminal_result = result
                    heartbeat_interrupted.set()
                    return

        def checkpoint():
            if heartbeat_interrupted.is_set():
                raise _HeartbeatStopped()

        def fail(code):
            try:
                result = store.fail_execution(*args, failure_code=code)
            except sqlite3.Error:
                return ExecutionResult(Outcome.STATUS_UNKNOWN)
            if result.outcome == Outcome.SAVED:
                return ExecutionResult(Outcome.FAILED, view=result.view)
            return result

        try:
            pending = _PendingWrites(context_id)
            answer = copy(self.answer_service)
            answer.checkpoint = checkpoint
            answer.evidence_writer = pending
            if answer.save_history is not None:
                answer.save_history = pending.save_history
            heartbeat = Thread(target=renew, daemon=True)
            heartbeat.start()
        except Exception:
            return fail('EXECUTION_START_FAILED')

        try:
            try:
                response = answer.answer(question, **scope)
                checkpoint()
                payload = json.dumps(public_answer_payload(response), ensure_ascii=False, separators=(',', ':'))
            finally:
                stopped.set()
                heartbeat.join()
            checkpoint()
            result = store.complete_execution(*args, payload, evidences=pending.evidences, history=pending.history)
            if result.outcome == Outcome.SAVED:
                return ExecutionResult(Outcome.COMPLETED, view=result.view)
            return result
        except Exception as error:
            if terminal_result is not None:
                return terminal_result
            if heartbeat_interrupted.is_set():
                return fail('HEARTBEAT_FAILED')
            code = 'GENERATION_UNAVAILABLE' if isinstance(error, AnswerGenerationError) else 'ANSWER_FAILED'
            return fail(code)
