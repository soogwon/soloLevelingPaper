"""로컬 PKCE 서버 연결 CLI를 검증한다."""

from base64 import urlsafe_b64encode
from hashlib import sha256
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from solo_leveling.infrastructure.storage.server_credentials import KeyringCredentialStore, StoredDeviceCredentials
from solo_leveling.interfaces.cli import connect_server as subject


SETTINGS = subject.ConnectSettings(
    "https://identity.example.com/authorize",
    "https://identity.example.com/token",
    "public-client",
    "openid profile",
    "https://api.example.com",
    "solo-leveling-api",
)


DEVICE_ID = "00000000-0000-0000-0000-000000000001"


class Response:
    status_code = 200
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


class Session:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/token"):
            return Response({"token_type": "Bearer", "access_token": "web-access-token"})
        if url.endswith("/v1/devices"):
            return Response({"device_id": DEVICE_ID})
        return Response({
            "device_id": DEVICE_ID,
            "token_type": "Bearer",
            "access_token": "slp_at_secret",
            "access_expires_at": "2026-11-12T10:15:00Z",
            "refresh_token": "slp_rt_secret",
            "refresh_expires_at": "2026-12-12T10:00:00Z",
        })

    def delete(self, url, **kwargs):
        self.calls.append(("DELETE", url, kwargs))
        return Response({})


class Server:
    def __init__(self, address, handler):
        self.server_port = address[1]
        self.address = address
        self.timeout = None
        self.closed = False

    def server_close(self):
        self.closed = True


def test_pkce_uses_high_entropy_verifier_and_s256_challenge():
    verifier, challenge = subject.create_pkce()

    assert 43 <= len(verifier) <= 128
    assert challenge == urlsafe_b64encode(sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def test_authorization_url_contains_state_loopback_and_s256_only():
    url = subject.authorization_url(SETTINGS, "http://127.0.0.1:54321/callback", "state", "challenge")
    query = parse_qs(urlsplit(url).query)

    assert query == {
        "response_type": ["code"],
        "client_id": ["public-client"],
        "redirect_uri": ["http://127.0.0.1:54321/callback"],
        "scope": ["openid profile"],
        "state": ["state"],
        "code_challenge": ["challenge"],
        "code_challenge_method": ["S256"],
        "audience": ["solo-leveling-api"],
    }


def test_connect_exchanges_code_registers_device_and_stores_only_in_keyring(monkeypatch):
    session, saved, opened = Session(), [], []
    monkeypatch.setattr(subject, "ThreadingHTTPServer", Server)
    monkeypatch.setattr(subject, "_wait_for_callback", lambda server, queue, state, timeout_seconds: {
        "state": [state], "code": ["authorization-code"]
    })

    device_id = subject.connect(
        SETTINGS,
        "My PC",
        session,
        SimpleNamespace(save=saved.append),
        browser_open=lambda url: opened.append(url) or True,
    )

    assert device_id == DEVICE_ID
    assert saved[0].access_token == "slp_at_secret" and saved[0].refresh_token == "slp_rt_secret"
    token_call = session.calls[0]
    assert token_call[1] == SETTINGS.token_url
    assert token_call[2]["data"]["code"] == "authorization-code"
    assert token_call[2]["data"]["code_verifier"]
    assert "client_secret" not in token_call[2]["data"]
    assert session.calls[1][2]["headers"] == {"Authorization": "Bearer web-access-token"}
    assert "slp_at_secret" not in repr(session.calls)
    assert opened and "code_challenge_method=S256" in opened[0]
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2Fcallback" in opened[0]


def test_keyring_failure_revokes_new_device(monkeypatch):
    session = Session()
    monkeypatch.setattr(subject, "ThreadingHTTPServer", Server)
    monkeypatch.setattr(subject, "_wait_for_callback", lambda server, queue, state, timeout_seconds: {
        "state": [state], "code": ["code"]
    })
    store = SimpleNamespace(save=lambda credentials: (_ for _ in ()).throw(RuntimeError("keyring unavailable")))

    with pytest.raises(RuntimeError, match="keyring"):
        subject.connect(SETTINGS, "My PC", session, store, browser_open=lambda url: True)

    assert any(method == "DELETE" and url.endswith(f"/v1/devices/{DEVICE_ID}") for method, url, _ in session.calls)


def test_keyring_store_serializes_credentials_without_using_server_url_as_username(monkeypatch):
    values = {}
    backend = SimpleNamespace(
        set_password=lambda service, account, payload: values.update({(service, account): payload}),
        get_password=lambda service, account: values.get((service, account)),
    )
    monkeypatch.setattr("solo_leveling.infrastructure.storage.server_credentials._native_keyring", lambda: backend)
    credentials = StoredDeviceCredentials(
        "https://api.example.com", DEVICE_ID, "access", "a-exp", "refresh", "r-exp"
    )
    store = KeyringCredentialStore()

    store.save(credentials)
    loaded = store.load(credentials.server_url)

    ((service, account), payload), = values.items()
    assert service == "solo-leveling-paper" and account.startswith("device:")
    assert credentials.server_url not in account
    assert loaded == credentials
    assert json.loads(payload)["refresh_token"] == "refresh"


@pytest.mark.parametrize("field", ["OIDC_AUTHORIZATION_URL", "OIDC_TOKEN_URL", "SERVER_API_URL"])
def test_connect_settings_require_https(field):
    values = SETTINGS.__dict__.copy()
    mapping = {
        "OIDC_AUTHORIZATION_URL": "authorization_url",
        "OIDC_TOKEN_URL": "token_url",
        "SERVER_API_URL": "server_url",
    }
    values[mapping[field]] = "http://attacker.example.com"
    with pytest.raises(ValueError, match="https"):
        subject.ConnectSettings(**values)


@pytest.mark.parametrize("data", [
    {"access_token": "token"},
    {"token_type": "MAC", "access_token": "token"},
    {"token_type": "Bearer", "access_token": ""},
])
def test_only_nonempty_bearer_access_tokens_are_accepted(data):
    with pytest.raises(ValueError):
        subject._bearer_access_token(data)


def test_oidc_scope_must_include_openid():
    values = SETTINGS.__dict__.copy()
    values["scopes"] = "profile"
    with pytest.raises(ValueError, match="openid"):
        subject.ConnectSettings(**values)


def test_auth0_redirect_port_is_fixed_configurable_and_validated():
    assert subject.load_settings({
        "OIDC_AUTHORIZATION_URL": SETTINGS.authorization_url,
        "OIDC_TOKEN_URL": SETTINGS.token_url,
        "OIDC_CLIENT_ID": SETTINGS.client_id,
        "SERVER_API_URL": SETTINGS.server_url,
        "OIDC_REDIRECT_PORT": "9123",
    }).redirect_port == 9123
    with pytest.raises(ValueError, match="OIDC_REDIRECT_PORT"):
        subject.load_settings({
            "OIDC_AUTHORIZATION_URL": SETTINGS.authorization_url,
            "OIDC_TOKEN_URL": SETTINGS.token_url,
            "OIDC_CLIENT_ID": SETTINGS.client_id,
            "SERVER_API_URL": SETTINGS.server_url,
            "OIDC_REDIRECT_PORT": "random",
        })

@pytest.mark.parametrize("failure", ["redirect", "wrong_device", "missing_refresh"])
def test_failed_credential_issue_cleans_up_only_registered_device(monkeypatch, failure):
    class FailedIssue(Session):
        def post(self, url, **kwargs):
            response = super().post(url, **kwargs)
            if url.endswith("/credentials"):
                if failure == "redirect":
                    response.status_code = 307
                elif failure == "wrong_device":
                    response.data["device_id"] = "other"
                else:
                    del response.data["refresh_token"]
            return response

    session, saved = FailedIssue(), []
    monkeypatch.setattr(subject, "ThreadingHTTPServer", Server)
    monkeypatch.setattr(subject, "_wait_for_callback", lambda *args, **kwargs: {"code": ["code"]})
    with pytest.raises(ValueError):
        subject.connect(SETTINGS, "My PC", session, SimpleNamespace(save=saved.append), lambda url: True)
    assert saved == []
    assert session.calls[-1][:2] == ("DELETE", f"{SETTINGS.server_url}/v1/devices/{DEVICE_ID}")
    assert all(kwargs["allow_redirects"] is False for _, _, kwargs in session.calls)


def test_native_keyring_ignores_configured_plugin(monkeypatch):
    import sys
    from solo_leveling.infrastructure.storage.server_credentials import _native_keyring

    expected = {
        "darwin": "keyring.backends.macOS",
        "win32": "keyring.backends.Windows",
        "linux": "keyring.backends.SecretService",
    }
    if sys.platform not in expected:
        pytest.skip("지원하는 운영체제에서 실행합니다.")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    assert type(_native_keyring()).__module__ == expected[sys.platform]
