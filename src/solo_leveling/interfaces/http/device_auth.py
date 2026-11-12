"""기기 access token을 서버의 활성 기기 등록 상태와 대조한다."""

from typing import Protocol

from .auth import AccessTokenRejected, AccessTokenVerificationUnavailable, AccessTokenVerifier, AuthenticatedPrincipal


class DeviceRegistry(Protocol):
    """서버 저장소에서 사용자 소유 기기의 활성 상태를 조회하는 포트."""

    def is_active(self, user_id: str, device_id: str) -> bool:
        """해당 사용자에게 연결되어 있고 해제되지 않은 기기인지 반환한다."""


class RejectAllDeviceRegistry:
    """기기 저장소가 연결되기 전 모든 기기 자격을 fail-closed로 거부한다."""

    def is_active(self, user_id: str, device_id: str) -> bool:
        return False


class DeviceAuthorizingTokenVerifier:
    """서명 검증 후 기기 자격에 서버 측 활성 상태 검사를 추가한다."""

    def __init__(self, verifier: AccessTokenVerifier, registry: DeviceRegistry):
        self._verifier = verifier
        self._registry = registry

    def verify(self, token: str) -> AuthenticatedPrincipal:
        principal = self._verifier.verify(token)
        if principal.credential_type == "web_session":
            return principal

        device_id = principal.device_id
        if device_id is None:
            raise AccessTokenRejected
        try:
            active = self._registry.is_active(principal.user_id, device_id)
        except AccessTokenVerificationUnavailable:
            raise
        except Exception:
            raise AccessTokenVerificationUnavailable from None
        if not active:
            raise AccessTokenRejected
        return principal
