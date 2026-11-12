"""중앙 API가 신뢰하는 인증 주체와 Bearer 검증 경계."""

from dataclasses import dataclass
from typing import Literal, Protocol


CredentialType = Literal["web_session", "device"]


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """검증된 토큰에서만 만들어지는 서버 내부 인증 주체."""

    user_id: str
    credential_type: CredentialType
    device_id: str | None = None

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("인증 주체의 user_id는 비어 있을 수 없습니다.")
        if self.credential_type not in ("web_session", "device"):
            raise ValueError("지원하지 않는 인증 자격 종류입니다.")
        if self.credential_type == "device" and not (self.device_id and self.device_id.strip()):
            raise ValueError("기기 인증 주체에는 device_id가 필요합니다.")
        if self.credential_type == "web_session" and self.device_id is not None:
            raise ValueError("웹 세션 인증 주체에는 device_id를 지정할 수 없습니다.")


class AccessTokenRejected(Exception):
    """토큰이 없거나 만료·폐기·위조되어 신뢰할 수 없음."""


class AccessTokenVerificationUnavailable(Exception):
    """공개 키를 조회할 수 없어 토큰 검증을 완료하지 못함."""


class AccessTokenVerifier(Protocol):
    """인증 제공자 토큰을 서버 내부 주체로 변환하는 포트."""

    def verify(self, token: str) -> AuthenticatedPrincipal:
        """검증된 주체를 반환하고 실패하면 AccessTokenRejected를 발생시킨다."""


class RejectAllTokenVerifier:
    """인증 제공자가 연결되기 전 운영 요청을 fail-closed로 거부한다."""

    def verify(self, token: str) -> AuthenticatedPrincipal:
        raise AccessTokenRejected


def create_auth_dependency(verifier: AccessTokenVerifier, web_session_verifier=None):
    """FastAPI Bearer 입력을 공급자 중립 검증기에 연결한다."""

    from fastapi import HTTPException, Request, Security, status
    from fastapi.security import HTTPBearer

    bearer = HTTPBearer(
        auto_error=False,
        scheme_name="ServerAccessToken",
        description="웹 세션 또는 연결된 로컬 기기에 발급된 access token",
    )

    def require_principal(credentials=Security(bearer), request: Request = None) -> AuthenticatedPrincipal:
        selected = verifier
        token = None
        if credentials is not None and credentials.scheme.lower() == "bearer":
            token = credentials.credentials
        elif web_session_verifier is not None and request is not None:
            token = request.cookies.get("slp_session")
            selected = web_session_verifier
        if not token:
            raise _unauthorized(HTTPException, status.HTTP_401_UNAUTHORIZED)
        try:
            return selected.verify(token)
        except AccessTokenVerificationUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "authentication_unavailable"},
            ) from None
        except AccessTokenRejected:
            raise _unauthorized(HTTPException, status.HTTP_401_UNAUTHORIZED) from None

    return require_principal


def _unauthorized(exception_type, status_code: int):
    return exception_type(
        status_code=status_code,
        detail={"code": "authentication_required"},
        headers={"WWW-Authenticate": "Bearer"},
    )
