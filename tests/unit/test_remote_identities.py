from contextlib import contextmanager

import pytest

from solo_leveling.application.evidence_qa.remote_contract import VerifiedRemoteIdentity
from solo_leveling.interfaces.http.remote_identities import (
    PostgresRemoteOwnerResolver,
    RemoteIdentityConflict,
)


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return Result(self.rows.pop(0))


class Pool:
    def __init__(self, *rows):
        self.conn = Connection(rows)

    @contextmanager
    def connection(self):
        yield self.conn


def identity(issuer="https://issuer.example/", subject="auth0|opaque"):
    return VerifiedRemoteIdentity(issuer, subject)


def test_resolves_only_exact_active_issuer_and_raw_subject_mapping():
    pool = Pool({"owner_user_id": "owner-1"})
    store = PostgresRemoteOwnerResolver(pool)

    assert store.resolve_owner(identity()) == "owner-1"
    assert pool.conn.calls[0][1] == ("https://issuer.example/", "auth0|opaque")
    assert "revoked_at IS NULL" in pool.conn.calls[0][0]


def test_missing_or_revoked_mapping_has_no_fallback():
    assert PostgresRemoteOwnerResolver(Pool(None)).resolve_owner(identity()) is None


def test_link_is_idempotent_only_for_same_owner_and_active_mapping():
    same = PostgresRemoteOwnerResolver(Pool({"owner_user_id": "owner-1", "revoked_at": None}))
    assert same.link_identity("owner-1", identity()) == "owner-1"

    other = PostgresRemoteOwnerResolver(Pool({"owner_user_id": "owner-2", "revoked_at": None}))
    with pytest.raises(RemoteIdentityConflict, match="^remote_identity_conflict$"):
        other.link_identity("owner-1", identity())

    revoked = PostgresRemoteOwnerResolver(Pool({"owner_user_id": "owner-1", "revoked_at": "now"}))
    with pytest.raises(RemoteIdentityConflict, match="^remote_identity_conflict$"):
        revoked.link_identity("owner-1", identity())


def test_revoke_is_owner_scoped_and_does_not_reassign():
    pool = Pool({"owner_user_id": "owner-1"})
    store = PostgresRemoteOwnerResolver(pool)

    assert store.revoke_identity("owner-1", identity()) is True
    assert pool.conn.calls[0][1] == (
        "https://issuer.example/",
        "auth0|opaque",
        "owner-1",
    )


@pytest.mark.parametrize(
    "value",
    [
        VerifiedRemoteIdentity(" https://issuer.example/", "subject"),
        VerifiedRemoteIdentity("https://issuer.example/", "subject "),
        VerifiedRemoteIdentity("x" * 2049, "subject"),
        VerifiedRemoteIdentity("https://issuer.example/", "x" * 513),
    ],
)
def test_rejects_noncanonical_or_oversized_identity(value):
    store = PostgresRemoteOwnerResolver(Pool())
    with pytest.raises(ValueError, match="^remote_identity_invalid$"):
        store.resolve_owner(value)
