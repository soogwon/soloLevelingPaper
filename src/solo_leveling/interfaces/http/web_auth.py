"""OIDC Authorization Code를 서버측 웹 세션 쿠키로 교환한다."""

from base64 import urlsafe_b64encode
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from hmac import compare_digest
from secrets import token_urlsafe
from typing import Any
from urllib.parse import urlencode, urlsplit

from .auth import AccessTokenRejected, AccessTokenVerificationUnavailable, AuthenticatedPrincipal
from .settings import WebAuthSettings


LOGIN_COOKIE = "__Host-slp_login"
SESSION_COOKIE = "slp_session"
CSRF_COOKIE = "csrf_token"


class WebAuthenticationRejected(Exception):
    pass


class WebAuthenticationUnavailable(Exception):
    pass


@dataclass(frozen=True)
class WebIdentity:
    user_id: str
    display_name: str
    email: str | None


@dataclass(frozen=True)
class WebSession:
    token: str
    csrf_token: str
    expires_at: datetime
    identity: WebIdentity


class PostgresWebSessionStore:
    def __init__(self, pool: Any):
        self._pool = pool

    def begin_login(self, state: str, verifier: str, return_to: str, expires_at: datetime) -> None:
        with _connection(self._pool) as conn:
            conn.execute("DELETE FROM web_login_attempts WHERE expires_at <= CURRENT_TIMESTAMP")
            conn.execute(
                """INSERT INTO web_login_attempts(state_hash, code_verifier, return_to, expires_at)
                   VALUES (%s, %s, %s, %s)""",
                (_hash(state), verifier, return_to, expires_at),
            )

    def consume_login(self, state: str) -> tuple[str, str]:
        with _connection(self._pool) as conn:
            row = conn.execute(
                """DELETE FROM web_login_attempts WHERE state_hash=%s AND expires_at>CURRENT_TIMESTAMP
                   RETURNING code_verifier, return_to""", (_hash(state),),
            ).fetchone()
        if row is None:
            raise WebAuthenticationRejected
        return row["code_verifier"], row["return_to"]

    def create_session(self, identity: WebIdentity, now: datetime) -> WebSession:
        token, csrf = token_urlsafe(48), token_urlsafe(32)
        expires = now + timedelta(days=7)
        with _connection(self._pool) as conn:
            conn.execute(
                """INSERT INTO server_users(owner_user_id, display_name, email)
                   VALUES (%s, %s, %s)
                   ON CONFLICT(owner_user_id) DO UPDATE SET display_name=excluded.display_name,
                   email=excluded.email, updated_at=CURRENT_TIMESTAMP""",
                (identity.user_id, identity.display_name, identity.email),
            )
            conn.execute(
                """INSERT INTO web_sessions(session_hash, csrf_hash, owner_user_id, expires_at)
                   VALUES (%s, %s, %s, %s)""",
                (_hash(token), _hash(csrf), identity.user_id, expires),
            )
        return WebSession(token, csrf, expires, identity)

    def authenticate(self, token: str, csrf_token: str | None = None) -> WebIdentity:
        sql = """SELECT s.owner_user_id, u.display_name, u.email FROM web_sessions s
                 JOIN server_users u USING(owner_user_id)
                 WHERE s.session_hash=%s AND s.revoked_at IS NULL
                   AND s.expires_at>CURRENT_TIMESTAMP"""
        params = [_hash(token)]
        if csrf_token is not None:
            sql += " AND s.csrf_hash=%s"
            params.append(_hash(csrf_token))
        with _connection(self._pool) as conn:
            row = conn.execute(sql, params).fetchone()
        if row is None:
            raise AccessTokenRejected
        return WebIdentity(row["owner_user_id"], row["display_name"], row["email"])

    def revoke(self, token: str, csrf_token: str) -> bool:
        with _connection(self._pool) as conn:
            row = conn.execute(
                """UPDATE web_sessions SET revoked_at=CURRENT_TIMESTAMP
                   WHERE session_hash=%s AND csrf_hash=%s AND revoked_at IS NULL
                   RETURNING owner_user_id""", (_hash(token), _hash(csrf_token)),
            ).fetchone()
        return row is not None


class WebSessionVerifier:
    def __init__(self, store: PostgresWebSessionStore):
        self._store = store

    def verify(self, token: str, csrf_token: str | None = None) -> AuthenticatedPrincipal:
        try:
            identity = self._store.authenticate(token, csrf_token)
        except WebAuthenticationUnavailable:
            raise AccessTokenVerificationUnavailable from None
        return AuthenticatedPrincipal(identity.user_id, "web_session")


class OidcWebAuthFlow:
    def __init__(self, settings: WebAuthSettings, store: PostgresWebSessionStore,
                 access_token_verifier: Any, session: Any, now=None):
        self.settings, self.store = settings, store
        self.verifier, self.session = access_token_verifier, session
        self.now = now or (lambda: datetime.now(timezone.utc))

    @classmethod
    def create(cls, settings, store, verifier):
        import requests
        session = requests.Session()
        session.trust_env = False
        return cls(settings, store, verifier, session)

    def start(self, return_to: str) -> tuple[str, str]:
        safe_return = _return_to(return_to, self.settings.app_origin)
        state, verifier = token_urlsafe(32), token_urlsafe(64)
        challenge = urlsafe_b64encode(sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        self.store.begin_login(state, verifier, safe_return, self.now() + timedelta(minutes=10))
        params = {"response_type": "code", "client_id": self.settings.client_id,
                  "redirect_uri": self.settings.redirect_uri, "scope": self.settings.scopes,
                  "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        return f"{self.settings.authorization_url}{'&' if '?' in self.settings.authorization_url else '?'}{urlencode(params)}", state

    def complete(self, code: str, state: str) -> tuple[WebSession, str]:
        if not code or not state:
            raise WebAuthenticationRejected
        verifier, return_to = self.store.consume_login(state)
        try:
            token_response = self.session.post(self.settings.token_url, data={
                "grant_type": "authorization_code", "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret, "code": code,
                "redirect_uri": self.settings.redirect_uri, "code_verifier": verifier,
            }, timeout=15, allow_redirects=False)
            if token_response.status_code >= 500 or token_response.status_code == 429:
                raise WebAuthenticationUnavailable
            if token_response.status_code != 200:
                raise WebAuthenticationRejected
            access_token = token_response.json().get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise WebAuthenticationRejected
            principal = self.verifier.verify(access_token)
            if principal.credential_type != "web_session":
                raise WebAuthenticationRejected
            info_response = self.session.get(self.settings.userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"}, timeout=15, allow_redirects=False)
            if info_response.status_code >= 500 or info_response.status_code == 429:
                raise WebAuthenticationUnavailable
            if info_response.status_code != 200:
                raise WebAuthenticationRejected
            info = info_response.json()
            if info.get("sub") != principal.user_id:
                raise WebAuthenticationRejected
            name = info.get("name") or info.get("nickname") or "Solo Leveling 사용자"
            email = info.get("email")
            if not isinstance(name, str) or not name.strip() or len(name) > 200:
                raise WebAuthenticationRejected
            if email is not None and (not isinstance(email, str) or len(email) > 320):
                raise WebAuthenticationRejected
            identity = WebIdentity(principal.user_id, name.strip(), email)
            return self.store.create_session(identity, self.now()), return_to
        except (WebAuthenticationRejected, AccessTokenRejected):
            raise WebAuthenticationRejected from None
        except AccessTokenVerificationUnavailable:
            raise WebAuthenticationUnavailable from None
        except WebAuthenticationUnavailable:
            raise
        except Exception:
            raise WebAuthenticationUnavailable from None


def _return_to(value: str, app_origin: str) -> str:
    parsed, allowed = urlsplit(value), urlsplit(app_origin)
    if (any(ord(char) < 32 or ord(char) == 127 for char in value)
            or (parsed.scheme, parsed.netloc) != (allowed.scheme, allowed.netloc)
            or parsed.username or parsed.password):
        raise WebAuthenticationRejected
    return value


def matching_tokens(first: str | None, second: str | None) -> bool:
    return bool(first and second and first.isascii() and second.isascii() and compare_digest(first, second))


def _hash(value: str) -> bytes:
    return sha256(value.encode("utf-8")).digest()


@contextmanager
def _connection(pool):
    try:
        with pool.connection() as conn:
            yield conn
    except (WebAuthenticationRejected, AccessTokenRejected):
        raise
    except Exception as exc:
        try:
            from psycopg import Error
            from psycopg_pool import PoolTimeout
            if isinstance(exc, (Error, PoolTimeout)):
                raise WebAuthenticationUnavailable from None
        except ImportError:
            pass
        raise
