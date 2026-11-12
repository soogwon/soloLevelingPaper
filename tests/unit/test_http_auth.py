"""중앙 HTTP API의 공급자 중립 인증 경계를 검증한다."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.auth import (
    AccessTokenRejected,
    AuthenticatedPrincipal,
    RejectAllTokenVerifier,
    create_auth_dependency,
)
from solo_leveling.interfaces.http.settings import HttpSettings


class StubVerifier:
    def __init__(self, principal=None):
        self.principal = principal
        self.tokens = []

    def verify(self, token):
        self.tokens.append(token)
        if self.principal is None:
            raise AccessTokenRejected
        return self.principal


def credentials(token="access-token"):
    return SimpleNamespace(scheme="Bearer", credentials=token)


def test_missing_or_rejected_token_returns_generic_bearer_challenge():
    for verifier, supplied in ((RejectAllTokenVerifier(), None), (StubVerifier(), credentials())):
        authenticate = create_auth_dependency(verifier)
        with pytest.raises(HTTPException) as caught:
            authenticate(supplied)

        assert caught.value.status_code == 401
        assert caught.value.detail == {"code": "authentication_required"}
        assert caught.value.headers == {"WWW-Authenticate": "Bearer"}


def test_verified_device_token_produces_principal_without_request_user_id():
    principal = AuthenticatedPrincipal("user-1", "device", "device-1")
    verifier = StubVerifier(principal)

    selected = create_auth_dependency(verifier)(credentials("verified-token"))

    assert selected == principal
    assert verifier.tokens == ["verified-token"]


def test_session_uses_supplied_verifier_even_when_its_length_is_zero():
    class EmptyVerifier(StubVerifier):
        def __len__(self):
            return 0

    verifier = EmptyVerifier(AuthenticatedPrincipal("user-1", "device", "device-1"))
    with TestClient(create_app(token_verifier=verifier)) as client:
        response = client.get("/v1/session", headers={"Authorization": "Bearer verified-token"})

    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "credential_type": "device", "device_id": "device-1"}
    assert verifier.tokens == ["verified-token"]


def test_session_rejects_bearer_token_when_verifier_is_none():
    with TestClient(create_app(token_verifier=None)) as client:
        response = client.get("/v1/session", headers={"Authorization": "Bearer unverified-token"})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_session_response_does_not_expose_internal_user_id():
    principal = AuthenticatedPrincipal("internal-user-id", "web_session")
    app = create_app(HttpSettings(environment="test"), StubVerifier(principal))
    route = next(route for route in app.routes if route.path == "/v1/session")

    response = route.endpoint(principal)

    assert response == {
        "authenticated": True,
        "credential_type": "web_session",
        "device_id": None,
    }
    assert "user_id" not in response


def test_openapi_marks_session_as_bearer_protected_but_health_as_public():
    schema = create_app(HttpSettings(environment="test")).openapi()

    assert schema["paths"]["/v1/session"]["get"]["security"] == [{"ServerAccessToken": []}]
    assert "security" not in schema["paths"]["/health"]["get"]


@pytest.mark.parametrize(
    "args",
    [
        ("", "web_session", None),
        ("user", "device", None),
        ("user", "web_session", "device"),
        ("user", "unknown", None),
    ],
)
def test_invalid_principals_are_rejected(args):
    with pytest.raises(ValueError):
        AuthenticatedPrincipal(*args)
