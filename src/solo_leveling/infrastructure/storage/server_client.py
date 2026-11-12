"""OS 자격 저장소의 기기 토큰으로 중앙 서버를 호출한다."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import Lock
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import UUID

from .server_credentials import KeyringCredentialStore, StoredDeviceCredentials


_refresh_lock = Lock()


class ServerConnectionRequired(Exception):
    """저장된 자격이 없거나 갱신할 수 없어 다시 연결해야 함."""


class ServerResponseInvalid(ServerConnectionRequired):
    """신뢰할 수 없는 형식의 서버 응답."""


class AuthenticatedServerClient:
    """access token 사용과 refresh 회전을 한 프로세스에서 직렬화한다."""

    def __init__(self, server_url: str, session: Any, credential_store: Any, now=None):
        self.server_url = _server_url(server_url)
        self._session = session
        self._credentials = credential_store
        self._now = now or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_keyring(cls, server_url: str) -> "AuthenticatedServerClient":
        import requests

        session = requests.Session()
        # 명시한 Bearer 헤더가 로컬 netrc 인증으로 교체되지 않게 한다.
        session.auth = lambda request: request
        return cls(server_url, session, KeyringCredentialStore())

    @classmethod
    def normalize_server_url(cls, server_url: str) -> str:
        """외부 호출 없이 런타임 설정의 서버 URL을 검증·정규화한다."""
        return _server_url(server_url)

    def request(self, method: str, path: str, *, expected_connection_id: str | None = None, **kwargs):
        url = self._request_url(path)
        credentials = self._load_credentials()
        if expected_connection_id is not None and self._connection_id(credentials) != expected_connection_id:
            raise ServerConnectionRequired
        refreshed = _parse_time(credentials.access_expires_at) <= self._now() + timedelta(seconds=30)
        if refreshed:
            credentials = self._refresh(credentials)
        response = self._send(method, url, credentials.access_token, **kwargs)
        if response.status_code == 401 and not refreshed:
            response.close()
            credentials = self._refresh(credentials)
            response = self._send(method, url, credentials.access_token, **kwargs)
        if response.status_code == 401:
            response.close()
            raise ServerConnectionRequired
        return response

    def connection_id(self) -> str:
        """현재 기기 연결을 토큰이나 서버 사용자 ID 노출 없이 로컬에서 구분한다."""
        return self._connection_id(self._load_credentials())

    def _connection_id(self, credentials: StoredDeviceCredentials) -> str:
        value = f"{self.server_url}\0{credentials.device_id}".encode("utf-8")
        return f"device-{sha256(value).hexdigest()}"

    def _load_credentials(self) -> StoredDeviceCredentials:
        credentials = self._credentials.load(self.server_url)
        if credentials is None or credentials.server_url != self.server_url:
            raise ServerConnectionRequired
        _validate_stored(credentials)
        return credentials

    def _refresh(self, expected: StoredDeviceCredentials) -> StoredDeviceCredentials:
        with _refresh_lock:
            current = self._load_credentials()
            if current.device_id != expected.device_id:
                raise ServerConnectionRequired
            if current.refresh_token != expected.refresh_token:
                if _parse_time(current.access_expires_at) > self._now():
                    return current
                raise ServerConnectionRequired
            if _parse_time(current.refresh_expires_at) <= self._now():
                raise ServerConnectionRequired

            response = self._session.post(
                f"{self.server_url}/v1/device-token/refresh",
                json={"refresh_token": current.refresh_token},
                timeout=15,
                allow_redirects=False,
            )
            if response.status_code in (400, 401, 403):
                raise ServerConnectionRequired
            if not 200 <= response.status_code < 300:
                return _raise_for_server_failure(response)
            try:
                data = response.json()
            except ValueError:
                raise ServerResponseInvalid from None
            refreshed = _validated_refresh(data, current, self.server_url, self._now())
            self._credentials.save(refreshed)
            return refreshed

    def _send(self, method: str, url: str, access_token: str, **kwargs):
        headers = {k: v for k, v in kwargs.pop("headers", {}).items()
                   if k.lower() not in ("authorization", "host")}
        kwargs.pop("allow_redirects", None)
        if kwargs.keys() - {"json", "params", "timeout", "data"}:
            raise ValueError("지원하지 않는 서버 요청 옵션입니다.")
        if "data" in kwargs and not isinstance(kwargs["data"], (str, bytes, dict, list, tuple, type(None))):
            raise ValueError("서버 요청 본문은 재전송 가능한 값이어야 합니다.")
        headers["Authorization"] = f"Bearer {access_token}"
        return self._session.request(
            method.upper(),
            url,
            headers=headers,
            timeout=kwargs.pop("timeout", 15),
            allow_redirects=False,
            **kwargs,
        )

    def _request_url(self, path: str) -> str:
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or not path.startswith("/v1/") or parsed.fragment:
            raise ValueError("서버 요청 경로는 /v1/ 아래의 상대 경로여야 합니다.")
        decoded = unquote(parsed.path)
        if "%" in decoded or "\\" in decoded or any(part in (".", "..") for part in decoded.split("/")):
            raise ValueError("서버 요청 경로에 허용되지 않는 구간이 있습니다.")
        return f"{self.server_url}{path}"


def _validated_refresh(data, current: StoredDeviceCredentials, server_url: str, now: datetime):
    if not isinstance(data, dict) or str(data.get("token_type", "")).lower() != "bearer":
        raise ServerResponseInvalid
    required = ("access_token", "access_expires_at", "refresh_token", "refresh_expires_at", "device_id")
    if any(not isinstance(data.get(name), str) or not data[name] for name in required):
        raise ServerResponseInvalid
    if data["device_id"] != current.device_id:
        raise ServerResponseInvalid
    if not data["access_token"].startswith("slp_at_") or not data["refresh_token"].startswith("slp_rt_"):
        raise ServerResponseInvalid
    if data["refresh_token"] == current.refresh_token or data["access_token"] == current.access_token:
        raise ServerResponseInvalid
    access_expiry = _parse_time(data["access_expires_at"])
    refresh_expiry = _parse_time(data["refresh_expires_at"])
    if access_expiry <= now or access_expiry > refresh_expiry:
        raise ServerResponseInvalid
    if refresh_expiry > _parse_time(current.refresh_expires_at):
        raise ServerResponseInvalid
    return StoredDeviceCredentials(
        server_url, current.device_id, data["access_token"], data["access_expires_at"],
        data["refresh_token"], data["refresh_expires_at"],
    )


def _validate_stored(credentials: StoredDeviceCredentials) -> None:
    try:
        UUID(credentials.device_id)
    except (ValueError, AttributeError):
        raise ServerResponseInvalid from None
    if not isinstance(credentials.access_token, str) or not isinstance(credentials.refresh_token, str):
        raise ServerResponseInvalid
    if not credentials.access_token.startswith("slp_at_") or not credentials.refresh_token.startswith("slp_rt_"):
        raise ServerResponseInvalid
    if _parse_time(credentials.access_expires_at) > _parse_time(credentials.refresh_expires_at):
        raise ServerResponseInvalid


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        raise ServerResponseInvalid from None
    if parsed.tzinfo is None:
        raise ServerResponseInvalid
    return parsed.astimezone(timezone.utc)


def _server_url(value: str) -> str:
    normalized = value.rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("SERVER_API_URL은 안전한 https URL이어야 합니다.")
    return normalized


def _raise_for_server_failure(response):
    response.raise_for_status()
    raise RuntimeError("서버 요청이 실패했습니다.")
