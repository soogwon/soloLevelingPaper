"""원격 MCP 전용 Auth0 설정과 JWT 경계를 검증한다."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from solo_leveling.interfaces.http.remote_mcp_auth import Auth0McpTokenVerifier
from solo_leveling.interfaces.http.settings import RemoteMcpSettings, load_remote_mcp_settings


ISSUER = "https://identity.example.com/"
RESOURCE = "https://solo-leveling-staging.soogyung.com/mcp"
SETTINGS = RemoteMcpSettings(
    enabled=True,
    resource_url=RESOURCE,
    issuer=ISSUER,
    audience=RESOURCE,
    jwks_url="https://identity.example.com/.well-known/jwks.json",
    required_scope="spike:read",
    allowed_hosts=("solo-leveling-staging.soogyung.com",),
    allowed_origins=("https://claude.ai",),
)


class KeyClient:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, _token):
        return SimpleNamespace(key=self.public_key)


def make_token(private_key, **overrides):
    now = datetime.now(timezone.utc)
    claims = {
        "iss": ISSUER,
        "aud": RESOURCE,
        "sub": "private-user-subject",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "scope": "openid spike:read",
        "azp": "published-client",
        **overrides,
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test", "typ": "JWT"})


def verify(verifier, token):
    return asyncio.run(verifier.verify_token(token))


def test_remote_mcp_settings_default_off_and_public_host_derived():
    assert load_remote_mcp_settings({}) == RemoteMcpSettings()
    values = {
        "REMOTE_MCP_SPIKE_ENABLED": "true",
        "REMOTE_MCP_RESOURCE_URL": RESOURCE,
        "REMOTE_MCP_OIDC_ISSUER": ISSUER,
        "REMOTE_MCP_OIDC_AUDIENCE": RESOURCE,
        "REMOTE_MCP_OIDC_JWKS_URL": "https://identity.example.com/jwks",
    }
    settings = load_remote_mcp_settings(values)
    assert settings.allowed_hosts == ("solo-leveling-staging.soogyung.com",)
    assert settings.allowed_origins == ("https://claude.ai",)


@pytest.mark.parametrize("change,match", [
    ({"REMOTE_MCP_OIDC_ISSUER": ""}, "ISSUER"),
    ({"REMOTE_MCP_RESOURCE_URL": "http://example.com/mcp"}, "https"),
    ({"REMOTE_MCP_RESOURCE_URL": "https://example.com/other"}, "/mcp"),
    ({"REMOTE_MCP_ALLOWED_HOSTS": "internal.example"}, "공개 resource hostname"),
    ({"REMOTE_MCP_ALLOWED_ORIGINS": "https://claude.ai/path"}, "origin"),
    ({"REMOTE_MCP_CLOCK_SKEW_SECONDS": "301"}, "0에서 300"),
])
def test_remote_mcp_settings_reject_incomplete_or_unsafe_values(change, match):
    values = {
        "REMOTE_MCP_SPIKE_ENABLED": "true",
        "REMOTE_MCP_RESOURCE_URL": RESOURCE,
        "REMOTE_MCP_OIDC_ISSUER": ISSUER,
        "REMOTE_MCP_OIDC_AUDIENCE": RESOURCE,
        "REMOTE_MCP_OIDC_JWKS_URL": "https://identity.example.com/jwks",
        **change,
    }
    with pytest.raises(ValueError, match=match):
        load_remote_mcp_settings(values)


def test_valid_auth0_access_token_maps_to_private_mcp_subject():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    result = verify(Auth0McpTokenVerifier(SETTINGS, KeyClient(private.public_key())), make_token(private))
    assert result is not None
    assert result.resource == RESOURCE
    assert result.scopes == ["openid", "spike:read"]
    assert result.client_id == "published-client"
    assert result.subject == f"{ISSUER}#private-user-subject"


@pytest.mark.parametrize("overrides", [
    {"iss": "https://attacker.example/"},
    {"aud": "https://solo-leveling-staging.soogyung.com/api"},
    {"scope": "openid"},
    {"exp": datetime.now(timezone.utc) - timedelta(minutes=5)},
    {"sub": ""},
    {"azp": ""},
])
def test_invalid_auth0_access_tokens_are_rejected(overrides):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = Auth0McpTokenVerifier(SETTINGS, KeyClient(private.public_key()))
    assert verify(verifier, make_token(private, **overrides)) is None


def test_wrong_algorithm_type_and_signing_key_are_rejected():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = Auth0McpTokenVerifier(SETTINGS, KeyClient(private.public_key()))
    claims = jwt.decode(make_token(private), options={"verify_signature": False})
    wrong_type = jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test", "typ": "at+jwt"})
    wrong_algorithm = jwt.encode(claims, "a-secure-test-key-that-is-at-least-32-bytes", algorithm="HS256",
                                 headers={"typ": "JWT"})
    assert verify(verifier, wrong_type) is None
    assert verify(verifier, wrong_algorithm) is None
    assert verify(verifier, make_token(attacker)) is None


def test_provider_failure_and_token_are_not_logged(caplog):
    class BrokenKeys:
        def get_signing_key_from_jwt(self, _token):
            raise RuntimeError("private-provider-diagnostic")

    token = "private-token-value"
    assert verify(Auth0McpTokenVerifier(SETTINGS, BrokenKeys()), token) is None
    assert "private-provider-diagnostic" not in caplog.text
    assert token not in caplog.text


@pytest.mark.parametrize('changes', [
    {'gty':'client-credentials'}, {'sub':'client@clients'},
    {'credential_type':'device'}, {'device_id':'private-device'},
    {'aud':'published-client'}, {'azp':None,'client_id':'published-client'},
])
def test_non_user_or_non_auth0_credentials_rejected(changes):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = Auth0McpTokenVerifier(SETTINGS, KeyClient(private.public_key()))
    assert verify(verifier, make_token(private, **changes)) is None


def test_web_audience_cannot_be_configured_as_remote_resource():
    from dataclasses import replace
    with pytest.raises(ValueError, match='audience'):
        replace(SETTINGS, audience='https://web.example/api')
