"""OIDC RS256 access token 검증 adapter를 검증한다."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import jwt
import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric import rsa

from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.auth import AccessTokenRejected
from solo_leveling.interfaces.http.oidc import OidcAccessTokenVerifier
from solo_leveling.interfaces.http.settings import OidcSettings, load_oidc_settings


ISSUER = "https://identity.example.com/"
AUDIENCE = "solo-leveling-api"


@pytest.fixture(scope="module")
def keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


class KeyClient:
    def __init__(self, public_key):
        self.public_key = public_key
        self.tokens = []

    def get_signing_key_from_jwt(self, token):
        self.tokens.append(token)
        return SimpleNamespace(key=self.public_key)


def make_token(private_key, *, typ="at+jwt", **overrides):
    now = datetime.now(timezone.utc)
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-1",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        **overrides,
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key", "typ": typ})


def verifier(public_key):
    settings = OidcSettings(ISSUER, AUDIENCE, "https://identity.example.com/.well-known/jwks.json")
    return OidcAccessTokenVerifier(settings, KeyClient(public_key))


def test_valid_web_and_device_tokens_create_expected_principals(keys):
    private, public = keys

    web = verifier(public).verify(make_token(private))
    device = verifier(public).verify(make_token(private, credential_type="device", device_id="device-1"))

    assert (web.user_id, web.credential_type, web.device_id) == ("user-1", "web_session", None)
    assert (device.user_id, device.credential_type, device.device_id) == ("user-1", "device", "device-1")


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "other-api"},
        {"iss": "https://attacker.example.com/"},
        {"exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
        {"sub": ""},
        {"credential_type": "device"},
        {"credential_type": "unknown"},
    ],
)
def test_invalid_or_untrusted_claims_are_rejected(keys, overrides):
    private, public = keys
    with pytest.raises(AccessTokenRejected):
        verifier(public).verify(make_token(private, **overrides))


def test_token_signed_by_another_key_is_rejected(keys):
    private, public = keys
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with pytest.raises(AccessTokenRejected):
        verifier(public).verify(make_token(attacker))


def test_oidc_settings_are_all_or_nothing_and_require_https():
    assert load_oidc_settings({}) is None
    with pytest.raises(ValueError, match="모두 설정"):
        load_oidc_settings({"OIDC_ISSUER": ISSUER})
    with pytest.raises(ValueError, match="https"):
        OidcSettings(ISSUER, AUDIENCE, "http://identity.example.com/jwks")
    with pytest.raises(ValueError, match="https"):
        OidcSettings("http://identity.example.com/", AUDIENCE, "https://identity.example.com/jwks")
    with pytest.raises(ValueError, match="자격정보"):
        OidcSettings(ISSUER, AUDIENCE, "https://user:secret@identity.example.com/jwks")

    assert load_oidc_settings({
        "OIDC_ISSUER": ISSUER,
        "OIDC_AUDIENCE": AUDIENCE,
        "OIDC_JWKS_URL": "https://identity.example.com/jwks",
    }) == OidcSettings(ISSUER, AUDIENCE, "https://identity.example.com/jwks")


@pytest.mark.parametrize("typ, expected", [("at+jwt", 200), ("application/at+jwt", 200), ("JWT", 401), (None, 401)])
def test_session_requires_access_token_type(keys, typ, expected):
    private, public = keys
    token = make_token(private, typ=typ)
    with TestClient(create_app(token_verifier=verifier(public))) as client:
        response = client.get("/v1/session", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == expected
    if expected == 401:
        assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("error, expected", [
    (jwt.PyJWKClientConnectionError("private diagnostic"), 503),
    (jwt.PyJWKClientError("unknown signing key"), 401),
])
def test_session_distinguishes_key_outage_from_unknown_key(keys, error, expected):
    private, _ = keys
    settings = OidcSettings(ISSUER, AUDIENCE, "https://identity.example.com/keys")
    key_client = Mock()
    key_client.get_signing_key_from_jwt.side_effect = error
    with TestClient(create_app(token_verifier=OidcAccessTokenVerifier(settings, key_client))) as client:
        response = client.get("/v1/session", headers={"Authorization": f"Bearer {make_token(private)}"})
    assert response.status_code == expected
    if expected == 503:
        assert response.json() == {"detail": {"code": "authentication_unavailable"}}
        assert "WWW-Authenticate" not in response.headers
    else:
        assert response.headers["WWW-Authenticate"] == "Bearer"
