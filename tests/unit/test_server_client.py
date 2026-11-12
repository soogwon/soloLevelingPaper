"""keyring 자격을 사용하는 중앙 서버 client를 검증한다."""

from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread

import pytest

from solo_leveling.infrastructure.storage.server_client import (
    AuthenticatedServerClient,
    ServerConnectionRequired,
    ServerResponseInvalid,
)
from solo_leveling.infrastructure.storage.server_credentials import StoredDeviceCredentials


NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
SERVER = "https://api.example.com"
DEVICE_ID = "00000000-0000-0000-0000-000000000001"


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def credentials(access="slp_at_access-1", refresh="slp_rt_refresh-1", access_delta=timedelta(minutes=10)):
    return StoredDeviceCredentials(
        SERVER, DEVICE_ID, access, iso(NOW + access_delta), refresh, iso(NOW + timedelta(days=20))
    )


class Store:
    def __init__(self, value):
        self.value = value
        self.saved = []

    def load(self, server_url):
        return self.value

    def save(self, value):
        self.saved.append(value)
        self.value = value


class Response:
    def __init__(self, status_code=200, data=None):
        self.status_code, self.data = status_code, data or {}

    def close(self):
        pass

    def json(self):
        return self.data

    def raise_for_status(self):
        if not 200 <= self.status_code < 300:
            raise RuntimeError("http failure")


class Session:
    def __init__(self, request_responses=()):
        self.request_responses = list(request_responses)
        self.requests, self.refreshes = [], []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.request_responses.pop(0) if self.request_responses else Response()

    def post(self, url, **kwargs):
        self.refreshes.append((url, kwargs))
        return Response(data={
            "token_type": "Bearer",
            "device_id": DEVICE_ID,
            "access_token": "slp_at_access-2",
            "access_expires_at": iso(NOW + timedelta(minutes=15)),
            "refresh_token": "slp_rt_refresh-2",
            "refresh_expires_at": iso(NOW + timedelta(days=20)),
        })


def test_connection_id_is_stable_and_does_not_expose_server_or_device_id():
    client = AuthenticatedServerClient(SERVER, Session(), Store(credentials()), now=lambda: NOW)

    first = client.connection_id()

    assert first == client.connection_id()
    assert first.startswith("device-")
    assert DEVICE_ID not in first
    assert "example.com" not in first


def test_valid_access_token_is_used_without_refresh_and_caller_cannot_override_it():
    store, session = Store(credentials()), Session()
    client = AuthenticatedServerClient(SERVER, session, store, now=lambda: NOW)

    client.request("post", "/v1/mcp/papers", headers={"Authorization": "attacker", "X-Test": "yes"}, json={})

    assert session.refreshes == []
    assert session.requests[0][2]["headers"] == {"Authorization": "Bearer slp_at_access-1", "X-Test": "yes"}
    assert session.requests[0][2]["allow_redirects"] is False


def test_expiring_access_token_refreshes_and_atomically_replaces_single_record():
    store, session = Store(credentials(access_delta=timedelta(seconds=10))), Session()
    client = AuthenticatedServerClient(SERVER, session, store, now=lambda: NOW)

    client.request("GET", "/v1/session")

    assert session.refreshes[0][1]["json"] == {"refresh_token": "slp_rt_refresh-1"}
    assert store.saved == [store.value]
    assert store.value.access_token == "slp_at_access-2" and store.value.refresh_token == "slp_rt_refresh-2"
    assert session.requests[0][2]["headers"]["Authorization"] == "Bearer slp_at_access-2"


def test_401_refreshes_once_and_retries_with_new_access_token():
    store = Store(credentials())
    session = Session([Response(401), Response(200)])
    client = AuthenticatedServerClient(SERVER, session, store, now=lambda: NOW)

    response = client.request("GET", "/v1/session")

    assert response.status_code == 200
    assert len(session.refreshes) == 1 and len(session.requests) == 2
    assert session.requests[1][2]["headers"]["Authorization"] == "Bearer slp_at_access-2"


def test_invalid_refresh_response_is_not_saved():
    store = Store(credentials(access_delta=timedelta(seconds=1)))
    session = Session()
    original_post = session.post

    def wrong_device(url, **kwargs):
        response = original_post(url, **kwargs)
        response.data["device_id"] = "other-device"
        return response

    session.post = wrong_device
    client = AuthenticatedServerClient(SERVER, session, store, now=lambda: NOW)

    with pytest.raises(ServerResponseInvalid):
        client.request("GET", "/v1/session")
    assert store.saved == []


def test_missing_or_expired_refresh_requires_reconnection_without_network():
    missing = AuthenticatedServerClient(SERVER, Session(), Store(None), now=lambda: NOW)
    expired_value = credentials(access_delta=timedelta(seconds=-1))
    expired_value = StoredDeviceCredentials(
        expired_value.server_url, expired_value.device_id, expired_value.access_token,
        expired_value.access_expires_at, expired_value.refresh_token, iso(NOW),
    )
    expired_session = Session()

    with pytest.raises(ServerConnectionRequired):
        missing.request("GET", "/v1/session")
    with pytest.raises(ServerConnectionRequired):
        AuthenticatedServerClient(SERVER, expired_session, Store(expired_value), now=lambda: NOW).request(
            "GET", "/v1/session"
        )
    assert expired_session.refreshes == []


def test_request_rejects_absolute_or_out_of_scope_paths():
    client = AuthenticatedServerClient(SERVER, Session(), Store(credentials()), now=lambda: NOW)
    for path in ("https://attacker.example/v1/x", "/health", "//attacker.example/v1/x"):
        with pytest.raises(ValueError):
            client.request("GET", path)


def test_concurrent_refresh_is_collapsed_to_one_rotation():
    store, session = Store(credentials(access_delta=timedelta(seconds=1))), Session()
    barrier, results, errors = Barrier(3), [], []

    expected = store.value

    def run():
        barrier.wait()
        try:
            other_client = AuthenticatedServerClient(SERVER, session, store, now=lambda: NOW)
            results.append(other_client._refresh(expected))
        except Exception as exc:
            errors.append(exc)

    threads = [Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == [] and len(results) == 2
    assert len(session.refreshes) == 1
    assert all(result.refresh_token == "slp_rt_refresh-2" for result in results)

@pytest.mark.parametrize("delta,requests", [(10, 1), (600, 2)])
def test_401_after_rotation_requires_reconnection_without_second_refresh(delta, requests):
    session = Session([Response(401), Response(401)])
    store = Store(credentials(access_delta=timedelta(seconds=delta)))
    client = AuthenticatedServerClient(SERVER, session, store, now=lambda: NOW)
    with pytest.raises(ServerConnectionRequired):
        client.request("GET", "/v1/session")
    assert len(session.refreshes) == 1
    assert len(session.requests) == requests


def test_client_rejects_parent_path_and_transport_overrides():
    session = Session()
    client = AuthenticatedServerClient(SERVER, session, Store(credentials()), now=lambda: NOW)
    with pytest.raises(ValueError):
        client.request("GET", "/v1/../health")
    with pytest.raises(ValueError):
        client.request("GET", "/v1/session", auth=("alternate", "identity"))
    assert not session.requests


def test_retry_never_uses_reconnected_device():
    from dataclasses import replace

    previous = credentials()
    store = Store(replace(previous, device_id="00000000-0000-0000-0000-000000000002"))
    client = AuthenticatedServerClient(SERVER, Session(), store, now=lambda: NOW)
    with pytest.raises(ServerConnectionRequired):
        client._refresh(previous)
