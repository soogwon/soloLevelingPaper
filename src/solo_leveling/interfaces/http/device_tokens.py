"""서버 발급 opaque 기기 토큰과 검증 adapter."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .auth import AccessTokenRejected, AccessTokenVerificationUnavailable, AccessTokenVerifier, AuthenticatedPrincipal


ACCESS_TOKEN_PREFIX = "slp_at_"
REFRESH_TOKEN_PREFIX = "slp_rt_"


@dataclass(frozen=True)
class DeviceTokenPair:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    device_id: str


class DeviceCredentialRejected(Exception):
    pass


class DeviceCredentialStore(Protocol):
    def issue_credentials(self, owner_user_id: str, device_id: str) -> DeviceTokenPair: ...
    def rotate_credentials(self, refresh_token: str) -> DeviceTokenPair: ...
    def authenticate_access_token(self, access_token: str) -> AuthenticatedPrincipal | None: ...


class OpaqueDeviceTokenVerifier:
    def __init__(self, store: DeviceCredentialStore):
        self._store = store

    def verify(self, token: str) -> AuthenticatedPrincipal:
        if not token.startswith(ACCESS_TOKEN_PREFIX):
            raise AccessTokenRejected
        try:
            principal = self._store.authenticate_access_token(token)
        except AccessTokenVerificationUnavailable:
            raise
        except Exception:
            raise AccessTokenVerificationUnavailable from None
        if principal is None:
            raise AccessTokenRejected
        return principal


class RoutingAccessTokenVerifier:
    """서버 opaque access token과 외부 OIDC token을 명확한 prefix로 분기한다."""

    def __init__(self, oidc_verifier: AccessTokenVerifier, device_verifier: AccessTokenVerifier):
        self._oidc = oidc_verifier
        self._device = device_verifier

    def verify(self, token: str) -> AuthenticatedPrincipal:
        if token.startswith(ACCESS_TOKEN_PREFIX):
            return self._device.verify(token)
        if token.startswith(REFRESH_TOKEN_PREFIX):
            raise AccessTokenRejected
        return self._oidc.verify(token)
