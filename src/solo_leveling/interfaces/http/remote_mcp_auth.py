"""Auth0 access token을 원격 MCP SDK의 인증 주체로 변환한다."""

import asyncio
from json import JSONDecodeError
from typing import Any

from mcp.server.auth.provider import AccessToken, TokenVerifier

from .settings import RemoteMcpSettings


class Auth0McpTokenVerifier(TokenVerifier):
    """전용 issuer·audience·scope를 모두 확인하는 fail-closed 검증기."""

    def __init__(self, settings: RemoteMcpSettings, jwks_client: Any | None = None):
        import jwt

        if not settings.enabled:
            raise ValueError("원격 MCP 인증 설정이 활성화되어야 합니다.")
        self._settings = settings
        self._jwks_client = jwks_client if jwks_client is not None else jwt.PyJWKClient(settings.jwks_url, timeout=5)

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token.strip():
            return None
        try:
            return await asyncio.to_thread(self._verify, token)
        except Exception:
            # 공급자 오류 본문과 토큰을 응답·로그에 노출하지 않는다.
            return None

    def _verify(self, token: str) -> AccessToken | None:
        import jwt

        try:
            header = jwt.get_unverified_header(token)
            # 이 스파이크의 Auth0 JWT profile은 typ=JWT를 사용한다. 다른 profile은 별도 검토한다.
            if header.get("alg") != "RS256" or header.get("typ") != "JWT":
                return None
            if not isinstance(header.get('kid'), str) or not header['kid'] or header.get('crit'):
                return None
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._settings.audience,
                issuer=self._settings.issuer,
                leeway=self._settings.clock_skew_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "scope"]},
            )
            subject = claims.get("sub")
            raw_scopes = claims.get("scope")
            client_id = claims.get("azp")
            expires_at = claims.get("exp")
            if (not isinstance(subject, str) or not subject.strip()
                    or subject.endswith('@clients')
                    or claims.get('gty') not in (None, 'authorization_code', 'refresh_token')
                    or 'credential_type' in claims or 'device_id' in claims
                    or not isinstance(raw_scopes, str)
                    or not isinstance(client_id, str) or client_id not in self._settings.allowed_client_ids
                    or not isinstance(expires_at, int) or isinstance(expires_at, bool)):
                return None
            if any(field in claims and type(claims[field]) is not int for field in ('exp', 'iat', 'nbf')):
                return None
            scopes = raw_scopes.split()
            if self._settings.required_scope not in scopes:
                return None
            return AccessToken(
                token=token,
                client_id=client_id,
                subject=f"{self._settings.issuer}#{subject}",
                resource=self._settings.resource_url,
                scopes=scopes,
                expires_at=expires_at,
            )
        except (jwt.PyJWTError, JSONDecodeError, ValueError, TypeError, KeyError):
            return None
