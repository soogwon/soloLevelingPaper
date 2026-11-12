"""외부 웹 OIDC callback과 보안 쿠키 세션 경계."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.auth import AccessTokenRejected, AuthenticatedPrincipal
from solo_leveling.interfaces.http.settings import WebAuthSettings
from solo_leveling.interfaces.http.papers import PaperPage
from solo_leveling.interfaces.http.web_auth import (
    OidcWebAuthFlow, WebAuthenticationRejected, WebIdentity, WebSession,
)


NOW = datetime(2026, 11, 12, 13, 0, tzinfo=timezone.utc)
SETTINGS = WebAuthSettings(
    "https://identity.example.com/authorize", "https://identity.example.com/token",
    "https://identity.example.com/userinfo", "web-client", "secret",
    "https://app.example.com/v1/auth/callback", "https://app.example.com",
)


class Store:
    def __init__(self):
        self.logins, self.sessions, self.revoked = [], {}, []

    def begin_login(self, state, verifier, return_to, expires_at):
        self.logins.append((state, verifier, return_to, expires_at))

    def consume_login(self, state):
        if not self.logins or self.logins[0][0] != state:
            raise WebAuthenticationRejected
        _, verifier, return_to, _ = self.logins.pop(0)
        return verifier, return_to

    def create_session(self, identity, now):
        session = WebSession("session-token", "csrf-token", now + timedelta(days=7), identity)
        self.sessions[session.token] = identity
        return session

    def authenticate(self, token, csrf_token=None):
        if token not in self.sessions or csrf_token not in (None, "csrf-token"):
            raise AccessTokenRejected
        return self.sessions[token]

    def revoke(self, token, csrf):
        self.revoked.append((token, csrf))
        return token in self.sessions and csrf == "csrf-token"


class PaperStore:
    def __init__(self):
        self.owners = []

    def list_papers(self, owner_user_id, **filters):
        self.owners.append(owner_user_id)
        return PaperPage((), filters["page"], filters["limit"], 0, 0)


class Response:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


class Http:
    def __init__(self, subject="user-1"):
        self.subject, self.posts, self.gets = subject, [], []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return Response(200, {"access_token": "verified-access"})

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return Response(200, {"sub": self.subject, "name": "Tester", "email": "t@example.com"})


class Verifier:
    def verify(self, token):
        assert token == "verified-access"
        return AuthenticatedPrincipal("user-1", "web_session")


def flow(store=None, http=None):
    return OidcWebAuthFlow(SETTINGS, store or Store(), Verifier(), http or Http(), now=lambda: NOW)


def test_login_uses_pkce_one_time_state_and_rejects_open_redirect():
    store = Store()
    selected = flow(store)
    url, _ = selected.start("https://app.example.com/#/papers")
    query = parse_qs(urlsplit(url).query)
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == [store.logins[0][0]]
    assert store.logins[0][2] == "https://app.example.com/#/papers"
    assert store.logins[0][1] not in url
    with pytest.raises(WebAuthenticationRejected):
        selected.start("https://evil.example/#/papers")
    with pytest.raises(WebAuthenticationRejected):
        selected.start("https://app.example.com/\nLocation:https://evil.example")


def test_callback_verifies_same_subject_and_creates_session():
    store, http = Store(), Http()
    selected = flow(store, http)
    auth_url, _ = selected.start("https://app.example.com/#/papers")
    state = parse_qs(urlsplit(auth_url).query)["state"][0]
    session, return_to = selected.complete("code-1", state)
    assert (session.identity.user_id, return_to) == ("user-1", "https://app.example.com/#/papers")
    assert http.posts[0][1]["data"]["code_verifier"]
    assert http.posts[0][1]["data"]["client_secret"] == "secret"
    with pytest.raises(WebAuthenticationRejected):
        selected.complete("code-1", state)


def test_callback_rejects_userinfo_for_another_subject():
    store = Store()
    selected = flow(store, Http(subject="other-user"))
    state = parse_qs(urlsplit(selected.start("https://app.example.com/")[0]).query)["state"][0]
    with pytest.raises(WebAuthenticationRejected):
        selected.complete("code", state)
    assert store.sessions == {}


def test_callback_maps_rejected_provider_token_to_authentication_failure():
    class RejectingVerifier:
        def verify(self, token):
            raise AccessTokenRejected

    store = Store()
    selected = OidcWebAuthFlow(SETTINGS, store, RejectingVerifier(), Http(), now=lambda: NOW)
    state = parse_qs(urlsplit(selected.start("https://app.example.com/")[0]).query)["state"][0]

    with pytest.raises(WebAuthenticationRejected):
        selected.complete("code", state)


def test_http_callback_sets_secure_cookies_me_and_csrf_logout():
    store = Store()
    papers = PaperStore()
    identity = WebIdentity("user-1", "Tester", "t@example.com")

    class Flow:
        now = staticmethod(lambda: NOW)
        def start(self, return_to):
            assert return_to == "https://app.example.com/#/papers"
            return "https://identity.example.com/authorize?state=s", "s"
        def complete(self, code, state):
            assert (code, state) == ("c", "s")
            return store.create_session(identity, NOW), "https://app.example.com/#/papers"

    app = create_app(token_verifier=Verifier(), web_auth_flow=Flow(), web_session_store=store,
                     paper_store=papers)
    client = TestClient(app, base_url="https://app.example.com", follow_redirects=False)
    login = client.get("/v1/auth/login", params={"provider": "google",
                       "return_to": "https://app.example.com/#/papers"})
    with TestClient(app, base_url="https://app.example.com", follow_redirects=False) as other:
        assert other.get("/v1/auth/callback?code=c&state=s").status_code == 401
    callback = client.get("/v1/auth/callback?code=c&state=s")
    assert client.get("/v1/auth/callback?code=c&state=s").status_code == 401
    assert client.post("/v1/devices", json={"display_name": "test"}).status_code == 403
    # 유효한 CSRF는 인증을 통과하고 미설정 기기 저장소까지 도달한다.
    assert client.post("/v1/devices", json={"display_name": "test"},
                       headers={"X-CSRF-Token": "csrf-token"}).status_code == 503
    me = client.get("/v1/me")
    paper_list = client.get("/v1/papers")
    rejected = client.post("/v1/auth/logout", headers={"X-CSRF-Token": "wrong"})
    logout = client.post("/v1/auth/logout", headers={"X-CSRF-Token": "csrf-token"})
    assert login.status_code == 302 and login.headers["location"].startswith("https://identity.example.com/")
    cookies = callback.headers.get_list("set-cookie")
    assert any("slp_session=" in value and "HttpOnly" in value and "Secure" in value and "SameSite=lax" in value
               for value in cookies)
    assert any("csrf_token=" in value and "HttpOnly" not in value and "Secure" in value for value in cookies)
    assert me.json() == {"user_id": "user-1", "display_name": "Tester", "email": "t@example.com"}
    assert paper_list.status_code == 200 and paper_list.json()["items"] == []
    assert papers.owners == ["user-1"]
    assert rejected.status_code == 403
    assert logout.status_code == 204
    assert store.revoked == [("session-token", "csrf-token")]

@pytest.mark.parametrize("endpoint", ["post", "get"])
def test_provider_outage_is_authentication_unavailable(endpoint):
    from solo_leveling.interfaces.http.web_auth import WebAuthenticationUnavailable

    http = Http()
    setattr(http, endpoint, lambda *args, **kwargs: Response(503, {}))
    selected = flow(http=http)
    _, state = selected.start("https://app.example.com/")
    with pytest.raises(WebAuthenticationUnavailable):
        selected.complete("code", state)
