"""OIDC 공급자의 RS256 access token 검증 adapter."""

from typing import Any

from .auth import AccessTokenRejected, AuthenticatedPrincipal
from .settings import OidcSettings


class OidcAccessTokenVerifier:
    """신뢰 설정과 JWKS로 JWT 서명 및 표준 claim을 검증한다."""

    def __init__(self, settings: OidcSettings, jwks_client: Any | None = None):
        import jwt

        self._settings = settings
        self._jwks_client = jwks_client or jwt.PyJWKClient(settings.jwks_url)

    def verify(self, token: str) -> AuthenticatedPrincipal:
        import jwt

        if not token.strip():
            raise AccessTokenRejected
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._settings.audience,
                issuer=self._settings.issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
            return _principal_from_claims(claims)
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise AccessTokenRejected from None


def _principal_from_claims(claims: dict[str, Any]) -> AuthenticatedPrincipal:
    subject = claims.get("sub")
    credential_type = claims.get("credential_type", "web_session")
    device_id = claims.get("device_id")
    if not isinstance(subject, str) or not isinstance(credential_type, str):
        raise AccessTokenRejected
    if device_id is not None and not isinstance(device_id, str):
        raise AccessTokenRejected
    try:
        return AuthenticatedPrincipal(subject, credential_type, device_id)  # type: ignore[arg-type]
    except ValueError:
        raise AccessTokenRejected from None
