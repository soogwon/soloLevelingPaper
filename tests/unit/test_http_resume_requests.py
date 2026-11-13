from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
from solo_leveling.interfaces.http.resume_requests import (
    PostgresResumeRequestStore, ResumeRequestConflict, ResumeRequestExpired,
    ResumeRequestResult,
)
from solo_leveling.interfaces.http.resume_routes import create_resume_router


PAPER = "00000000-0000-0000-0000-000000000101"
RECORD = "00000000-0000-0000-0000-000000000201"


class MemoryStore:
    def __init__(self):
        self.calls = []

    def create(self, owner, key, **payload):
        self.calls.append(("create", owner, key, payload))
        return ResumeRequestResult(
            "00000000-0000-0000-0000-000000000301", "resume_code",
            datetime.now(timezone.utc) + timedelta(minutes=10), "prompt", "created",
            "local_client_required",
        )

    def resolve(self, owner, code):
        self.calls.append(("resolve", owner, code))
        return {"status": "prepared"}


def _endpoint(path):
    store = MemoryStore()
    router = create_resume_router(lambda: None, store)
    return next(route.endpoint for route in router.routes if route.path == path), store


def test_web_creates_resume_code_for_owned_selection():
    endpoint, store = _endpoint("/v1/resume-requests")
    body = SimpleNamespace(
        paper_id=UUID(PAPER), version_id="v1", learning_record_id=UUID(RECORD),
        annotation_id=None,
    )
    result = endpoint(
        body, AuthenticatedPrincipal("user-1", "web_session"),
        "resume-request-0001",
    )
    assert result.code == "resume_code"
    assert store.calls == [("create", "user-1", "resume-request-0001", {
        "paper_id": PAPER, "version_id": "v1", "learning_record_id": RECORD,
        "annotation_id": None,
    })]


def test_only_device_of_same_authenticated_user_resolves_code():
    endpoint, store = _endpoint("/v1/mcp/resume-requests/resolve")
    assert endpoint(SimpleNamespace(code="resume_code"),
                    AuthenticatedPrincipal("user-1", "device", "device-1")) == {
                        "status": "prepared"
                    }
    assert store.calls == [("resolve", "user-1", "resume_code")]
    with pytest.raises(HTTPException) as failure:
        endpoint(SimpleNamespace(code="resume_code"),
                 AuthenticatedPrincipal("user-1", "web_session"))
    assert failure.value.status_code == 403


def test_invalid_idempotency_key_is_rejected_before_store():
    endpoint, store = _endpoint("/v1/resume-requests")
    with pytest.raises(HTTPException) as failure:
        endpoint(SimpleNamespace(paper_id=UUID(PAPER), version_id="v1",
                                 learning_record_id=UUID(RECORD), annotation_id=None),
                 AuthenticatedPrincipal("user-1", "web_session"), "short")
    assert failure.value.status_code == 422
    assert not store.calls


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if sql.lstrip().startswith("INSERT"):
            return Result()
        return Result(next(self.rows))


class Pool:
    def __init__(self, rows):
        self.conn = Connection(rows)

    def connection(self):
        return nullcontext(self.conn)


def test_store_replays_same_idempotent_request_and_rejects_changed_payload():
    now = datetime.now(timezone.utc) + timedelta(minutes=5)
    previous = {
        "payload_hash": b"bad", "resume_request_id": "request", "code": "resume_code",
        "expires_at": now, "prompt_text": "prompt", "status": "created", "reason": None,
    }
    store = PostgresResumeRequestStore(Pool([previous]))
    with pytest.raises(ResumeRequestConflict):
        store.create("user-1", "resume-request-0001", paper_id=PAPER, version_id="v1",
                     learning_record_id=RECORD, annotation_id=None)


def test_store_replays_same_idempotent_request():
    from solo_leveling.interfaces.http.resume_requests import _payload_hash

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    previous = {
        "payload_hash": _payload_hash(PAPER, "v1", RECORD, None),
        "resume_request_id": "request", "code": "resume_code",
        "expires_at": expires_at, "prompt_text": "prompt", "status": "created",
        "reason": "local_client_required",
    }
    store = PostgresResumeRequestStore(Pool([previous]))

    result = store.create(
        "user-1", "resume-request-0001", paper_id=PAPER, version_id="v1",
        learning_record_id=RECORD, annotation_id=None,
    )

    assert result.code == "resume_code"
    assert result.expires_at == expires_at
    assert len(store._pool.conn.calls) == 1


def test_store_rejects_expired_code_without_changing_learning_state():
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    row = {
        "resume_request_id": "request", "paper_id": PAPER, "title": "Paper",
        "version_id": "v1", "learning_record_id": RECORD,
        "last_position": {"pdf_page": 3}, "annotation_id": None,
        "annotation_kind": None, "annotation_body": None, "annotation_segments": None,
        "file_name": "paper.pdf", "expires_at": expired,
    }
    store = PostgresResumeRequestStore(Pool([row]))

    with pytest.raises(ResumeRequestExpired):
        store.resolve("user-1", "resume_code")

    assert len(store._pool.conn.calls) == 1
    assert store._pool.conn.calls[0][0].lstrip().startswith("SELECT")


@pytest.mark.parametrize("same_payload", [True, False])
def test_store_handles_concurrent_idempotency_winner(same_payload):
    from solo_leveling.interfaces.http.resume_requests import _payload_hash

    winner = {
        "payload_hash": _payload_hash(PAPER, "v1" if same_payload else "v2", RECORD, None),
        "resume_request_id": "winner", "code": "winning_code",
        "expires_at": datetime.now(timezone.utc), "prompt_text": "prompt",
        "status": "created", "reason": None,
    }
    pool = Pool([None, {"file_name": "paper.pdf"}, winner])
    store = PostgresResumeRequestStore(pool)
    args = dict(paper_id=PAPER, version_id="v1", learning_record_id=RECORD, annotation_id=None)
    if same_payload:
        assert store.create("user-1", "resume-request-0001", **args).code == "winning_code"
    else:
        with pytest.raises(ResumeRequestConflict):
            store.create("user-1", "resume-request-0001", **args)
    assert "ON CONFLICT (owner_user_id, idempotency_key) DO NOTHING" in pool.conn.calls[2][0]
