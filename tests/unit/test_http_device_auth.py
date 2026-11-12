"""기기 자격의 서버 측 활성 상태 검사를 검증한다."""

from types import SimpleNamespace

import pytest

from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.auth import (
    AccessTokenRejected,
    AccessTokenVerificationUnavailable,
    AuthenticatedPrincipal,
)
from solo_leveling.interfaces.http.device_auth import (
    DeviceAuthorizingTokenVerifier,
    RejectAllDeviceRegistry,
)
from solo_leveling.interfaces.http import oidc


class PrincipalVerifier:
    def __init__(self, principal):
        self.principal = principal
        self.tokens = []

    def verify(self, token):
        self.tokens.append(token)
        return self.principal


class Registry:
    def __init__(self, active=()):
        self.active = set(active)
        self.lookups = []

    def is_active(self, user_id, device_id):
        self.lookups.append((user_id, device_id))
        return (user_id, device_id) in self.active


def test_active_device_for_same_user_is_accepted():
    principal = AuthenticatedPrincipal("user-1", "device", "device-1")
    registry = Registry({("user-1", "device-1")})
    verifier = DeviceAuthorizingTokenVerifier(PrincipalVerifier(principal), registry)

    assert verifier.verify("signed-token") == principal
    assert registry.lookups == [("user-1", "device-1")]


@pytest.mark.parametrize(
    "active",
    [set(), {("other-user", "device-1")}, {("user-1", "other-device")}],
)
def test_missing_revoked_or_other_users_device_is_rejected_without_distinction(active):
    principal = AuthenticatedPrincipal("user-1", "device", "device-1")
    verifier = DeviceAuthorizingTokenVerifier(PrincipalVerifier(principal), Registry(active))

    with pytest.raises(AccessTokenRejected):
        verifier.verify("signed-token")


def test_default_registry_rejects_every_device():
    principal = AuthenticatedPrincipal("user-1", "device", "device-1")
    verifier = DeviceAuthorizingTokenVerifier(PrincipalVerifier(principal), RejectAllDeviceRegistry())

    with pytest.raises(AccessTokenRejected):
        verifier.verify("signed-token")


def test_app_uses_supplied_registry_even_when_its_length_is_zero(monkeypatch):
    class EmptyRegistry(Registry):
        def __len__(self):
            return 0

    principal = AuthenticatedPrincipal("user-1", "device", "device-1")
    registry = EmptyRegistry({("user-1", "device-1")})
    monkeypatch.setattr(oidc, "OidcAccessTokenVerifier", lambda settings: PrincipalVerifier(principal))
    monkeypatch.setenv("OIDC_ISSUER", "https://identity.example.com/")
    monkeypatch.setenv("OIDC_AUDIENCE", "solo-leveling-api")
    monkeypatch.setenv("OIDC_JWKS_URL", "https://identity.example.com/jwks")

    app = create_app(device_registry=registry)
    route = next(route for route in app.routes if route.path == "/v1/session")
    authenticate = route.dependant.dependencies[0].call
    selected = authenticate(SimpleNamespace(scheme="Bearer", credentials="signed-token"))

    assert selected == principal
    assert registry.lookups == [("user-1", "device-1")]


def test_web_session_does_not_require_device_lookup():
    principal = AuthenticatedPrincipal("user-1", "web_session")
    registry = Registry()
    verifier = DeviceAuthorizingTokenVerifier(PrincipalVerifier(principal), registry)

    assert verifier.verify("signed-token") == principal
    assert registry.lookups == []


@pytest.mark.parametrize("error", [RuntimeError("database down"), AccessTokenVerificationUnavailable()])
def test_registry_failure_is_authentication_unavailable(error):
    principal = AuthenticatedPrincipal("user-1", "device", "device-1")

    class BrokenRegistry:
        def is_active(self, user_id, device_id):
            raise error

    verifier = DeviceAuthorizingTokenVerifier(PrincipalVerifier(principal), BrokenRegistry())
    with pytest.raises(AccessTokenVerificationUnavailable):
        verifier.verify("signed-token")
