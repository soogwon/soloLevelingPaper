"""서버 발급 opaque 기기 토큰의 저장·검증·회전을 검증한다."""

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import Response

from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.auth import AccessTokenRejected, AuthenticatedPrincipal
from solo_leveling.interfaces.http.device_tokens import (
    ACCESS_TOKEN_PREFIX,
    DeviceCredentialRejected,
    DeviceTokenPair,
    OpaqueDeviceTokenVerifier,
    RoutingAccessTokenVerifier,
)
from solo_leveling.interfaces.http.devices import PostgresDeviceStore, UnavailableDeviceStore


NOW = datetime.now(timezone.utc)
DEVICE_ID = "00000000-0000-0000-0000-000000000001"


class Result:
    def __init__(self, one=None, many=()):
        self.one, self.many = one, many

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class Connection:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((" ".join(sql.split()), params))
        return self.results.pop(0)


class Pool:
    def __init__(self, results):
        self.conn = Connection(results)

    def connection(self):
        return nullcontext(self.conn)


def test_issued_tokens_are_random_and_only_hashes_reach_postgres():
    pool = Pool([Result(one={"active": 1}), Result(), Result()])
    pair = PostgresDeviceStore(pool).issue_credentials("user-1", DEVICE_ID)

    assert pair.access_token.startswith("slp_at_")
    assert pair.refresh_token.startswith("slp_rt_")
    insert_params = pool.conn.calls[2][1]
    assert isinstance(insert_params[3], bytes) and len(insert_params[3]) == 32
    assert isinstance(insert_params[5], bytes) and len(insert_params[5]) == 32
    assert pair.access_token not in repr(pool.conn.calls)
    assert pair.refresh_token not in repr(pool.conn.calls)


def test_access_token_lookup_returns_device_principal_and_uses_hash():
    pool = Pool([Result(one={"owner_user_id": "user-1", "device_id": DEVICE_ID})])
    token = ACCESS_TOKEN_PREFIX + "a" * 43

    principal = PostgresDeviceStore(pool).authenticate_access_token(token)

    assert principal == AuthenticatedPrincipal("user-1", "device", DEVICE_ID)
    assert pool.conn.calls[0][1][0] != token
    assert isinstance(pool.conn.calls[0][1][0], bytes)


@pytest.mark.parametrize("remaining", [timedelta(days=20), timedelta(minutes=3)])
def test_refresh_rotates_both_tokens_without_extending_family_expiry(remaining):
    expires = NOW + remaining
    row = {
        "credential_id": "00000000-0000-0000-0000-000000000010",
        "family_id": "00000000-0000-0000-0000-000000000020",
        "device_id": DEVICE_ID,
        "refresh_expires_at": expires,
        "revoked_at": None,
        "device_revoked_at": None,
    }
    pool = Pool([Result(one={"device_id": DEVICE_ID}), Result(one=row), Result(), Result()])

    pair = PostgresDeviceStore(pool).rotate_credentials("slp_rt_" + "r" * 43)

    assert pair.refresh_expires_at == expires
    assert pair.access_expires_at <= expires
    assert "SET revoked_at" in pool.conn.calls[2][0]
    assert pool.conn.calls[3][1][1] == row["family_id"]


def test_replayed_refresh_revokes_family_then_returns_generic_rejection():
    row = {
        "credential_id": "c",
        "family_id": "family",
        "device_id": DEVICE_ID,
        "refresh_expires_at": NOW + timedelta(days=1),
        "revoked_at": NOW,
        "device_revoked_at": None,
    }
    pool = Pool([Result(one={"device_id": DEVICE_ID}), Result(one=row), Result()])

    with pytest.raises(DeviceCredentialRejected):
        PostgresDeviceStore(pool).rotate_credentials("slp_rt_" + "r" * 43)

    assert "WHERE family_id = %s" in pool.conn.calls[2][0]
    assert pool.conn.calls[2][1] == ("family",)


def test_opaque_and_oidc_tokens_use_separate_verifiers():
    principal = AuthenticatedPrincipal("user-1", "device", DEVICE_ID)
    store = SimpleNamespace(authenticate_access_token=lambda token: principal)
    opaque = OpaqueDeviceTokenVerifier(store)
    oidc = SimpleNamespace(verify=lambda token: AuthenticatedPrincipal("user-1", "web_session"))
    verifier = RoutingAccessTokenVerifier(oidc, opaque)

    assert verifier.verify(ACCESS_TOKEN_PREFIX + "x").credential_type == "device"
    with pytest.raises(AccessTokenRejected):
        verifier.verify("slp_rt_" + "r" * 43)
    assert verifier.verify("external-jwt").credential_type == "web_session"

    with pytest.raises(AccessTokenRejected):
        OpaqueDeviceTokenVerifier(SimpleNamespace(authenticate_access_token=lambda token: None)).verify(
            ACCESS_TOKEN_PREFIX + "missing"
        )

    with pytest.raises(AccessTokenRejected):
        OpaqueDeviceTokenVerifier(UnavailableDeviceStore()).verify(ACCESS_TOKEN_PREFIX + "unconfigured")


def test_issue_and_refresh_routes_return_no_store_credentials():
    pair = DeviceTokenPair("slp_at_access", NOW + timedelta(minutes=15), "slp_rt_refresh", NOW + timedelta(days=30), DEVICE_ID)

    class Store:
        def is_active(self, user_id, device_id):
            return True

        def issue_credentials(self, owner_user_id, device_id):
            assert (owner_user_id, device_id) == ("user-1", DEVICE_ID)
            return pair

        def rotate_credentials(self, refresh_token):
            assert refresh_token == "slp_rt_refresh"
            return pair

        def authenticate_access_token(self, access_token):
            return None

    app = create_app(device_store=Store())
    principal = AuthenticatedPrincipal("user-1", "web_session")
    issue_route = next(route.endpoint for route in app.routes if route.path == "/v1/devices/{device_id}/credentials")
    refresh_route = next(route.endpoint for route in app.routes if route.path == "/v1/device-token/refresh")
    issue_response, refresh_response = Response(), Response()

    issued = issue_route(UUID(DEVICE_ID), principal, issue_response)
    refreshed = refresh_route(SimpleNamespace(refresh_token="slp_rt_refresh"), refresh_response)

    assert issued.access_token == refreshed.access_token == pair.access_token
    assert issue_response.headers["Cache-Control"] == refresh_response.headers["Cache-Control"] == "no-store"
    assert issue_response.headers["Pragma"] == refresh_response.headers["Pragma"] == "no-cache"


def test_non_ascii_device_tokens_are_rejected_before_database_lookup():
    store = PostgresDeviceStore(Pool([]))
    assert store.authenticate_access_token("slp_at_" + "기" * 43) is None
    with pytest.raises(DeviceCredentialRejected):
        store.rotate_credentials("slp_rt_" + "기" * 43)
