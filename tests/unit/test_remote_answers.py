from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest

from solo_leveling.application.evidence_qa.remote_answer import PreparedRemoteAnswer
from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchBinding
from solo_leveling.application.provider_dispatch import DispatchIntent
from solo_leveling.domain.evidence_qa import AnswerResult, AnswerStatus, Citation, Claim
from solo_leveling.domain.models import Evidence
from solo_leveling.domain.semantic_verification import VerificationLevel
from solo_leveling.interfaces.http.remote_answers import PostgresRemoteAnswerStore, RemoteAnswerSaveRejected


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class Conn:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return Result(self.rows.pop(0))


class Pool:
    def __init__(self, *rows):
        self.conn = Conn(rows)

    @contextmanager
    def connection(self):
        yield self.conn


BINDING = RemoteSearchBinding(
    "owner", "context", "paper", "version", "publication", "parse", "translation", "set", "fp"
)
INTENT = DispatchIntent("owner", "execution", "lease", "hash", "storage", "ai", "notice")


def prepared():
    evidence = Evidence("evidence", "chunk", "합성 번역", "Synthetic source.")
    citation = Citation(
        "evidence", "chunk", "version", "parse", "translation", None, 6, "합성 번역", "Synthetic source."
    )
    result = AnswerResult(
        AnswerStatus.OK,
        "합성 답변",
        (Claim("claim", "합성 주장", ("evidence",)),),
        (citation,),
        None,
    )
    response = NS(
        context_id="context",
        result=result,
        verification_level=VerificationLevel.MODEL_CHECKED,
        verification_history={"status": "saved"},
    )
    return PreparedRemoteAnswer(BINDING, response, (evidence,))


def test_saves_evidence_and_payload_before_completing_execution():
    pool = Pool({"locked": True}, {"evidence_id": "evidence"}, {"execution_id": "execution"})

    payload = PostgresRemoteAnswerStore(pool).save(INTENT, prepared())

    assert payload["context_id"] == "context" and payload["verification_level"] == "model_checked"
    assert payload["claims"][0]["evidence_ids"] == ["evidence"]
    statements = [sql for sql, _ in pool.conn.calls]
    assert "FOR UPDATE OF e,p" in statements[0]
    assert "position(%s in c.original_text)>0" in statements[1]
    assert "position(%s in tc.translated_text)>0" in statements[1]
    assert statements[1].startswith("INSERT INTO server_processing_evidences")
    assert statements[2].startswith("UPDATE server_remote_executions")
    assert "lease_expires_at>clock_timestamp()" in statements[2]


def test_scope_or_lease_loss_rejects_before_evidence_write():
    pool = Pool(None)

    with pytest.raises(RemoteAnswerSaveRejected, match="remote_answer_lease_or_scope_lost"):
        PostgresRemoteAnswerStore(pool).save(INTENT, prepared())

    assert len(pool.conn.calls) == 1


def test_source_quote_mismatch_aborts_before_completion():
    pool = Pool({"locked": True}, None)

    with pytest.raises(RemoteAnswerSaveRejected, match="remote_answer_source_mismatch"):
        PostgresRemoteAnswerStore(pool).save(INTENT, prepared())

    assert not any(sql.startswith("UPDATE server_remote_executions") for sql, _ in pool.conn.calls)


def test_mismatched_evidence_and_citation_fail_before_database():
    value = prepared()
    bad = PreparedRemoteAnswer(BINDING, value.response, (Evidence("other", "chunk", "합성 번역", "Synthetic source."),))
    pool = Pool()

    with pytest.raises(ValueError, match="remote_answer_evidence_invalid"):
        PostgresRemoteAnswerStore(pool).save(INTENT, bad)

    assert pool.conn.calls == []


def test_insufficient_answer_without_evidence_can_complete_atomically():
    result = AnswerResult(AnswerStatus.INSUFFICIENT_EVIDENCE, "근거 부족", (), (), None)
    response = NS(
        context_id="context",
        result=result,
        verification_level=VerificationLevel.STRUCTURAL_ONLY,
        verification_history={"status": "not_used"},
    )
    pool = Pool({"locked": True}, {"execution_id": "execution"})

    payload = PostgresRemoteAnswerStore(pool).save(INTENT, PreparedRemoteAnswer(BINDING, response, ()))

    assert payload["status"] == "insufficient_evidence"
    assert len(pool.conn.calls) == 2


def test_foreign_binding_owner_fails_before_database():
    from dataclasses import replace

    value = prepared()
    pool = Pool()
    with pytest.raises(ValueError, match="remote_answer_scope_invalid"):
        PostgresRemoteAnswerStore(pool).save(INTENT, replace(value, binding=replace(BINDING, owner_user_id="other")))
    assert pool.conn.calls == []
