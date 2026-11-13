"""시스템 브라우저 PKCE 로그인으로 로컬 설치를 중앙 서버 기기에 연결한다."""

import argparse
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from queue import Empty, Queue
from secrets import token_urlsafe
import sys
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID
import webbrowser

from solo_leveling.infrastructure.storage.server_credentials import KeyringCredentialStore, StoredDeviceCredentials


@dataclass(frozen=True)
class ConnectSettings:
    authorization_url: str
    token_url: str
    client_id: str
    scopes: str
    server_url: str
    audience: str | None = None
    redirect_port: int = 8765

    def __post_init__(self) -> None:
        for name, value in (("OIDC_AUTHORIZATION_URL", self.authorization_url),
                            ("OIDC_TOKEN_URL", self.token_url), ("SERVER_API_URL", self.server_url)):
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise ValueError(f"{name}은 안전한 https URL이어야 합니다.")
        if urlsplit(self.server_url).query:
            raise ValueError("SERVER_API_URL에는 쿼리를 지정할 수 없습니다.")
        if not self.client_id.strip() or not self.scopes.strip():
            raise ValueError("OIDC client 설정이 비어 있습니다.")
        if "openid" not in self.scopes.split():
            raise ValueError("OIDC_CLIENT_SCOPES에는 openid가 필요합니다.")
        if not 1 <= self.redirect_port <= 65535:
            raise ValueError("OIDC_REDIRECT_PORT는 1에서 65535 사이여야 합니다.")


def load_settings(source=None) -> ConnectSettings:
    values = os.environ if source is None else source
    required = ("OIDC_AUTHORIZATION_URL", "OIDC_TOKEN_URL", "OIDC_CLIENT_ID", "SERVER_API_URL")
    missing = [name for name in required if not values.get(name, "").strip()]
    if missing:
        raise ValueError("필수 연결 설정이 없습니다.")
    return ConnectSettings(
        values["OIDC_AUTHORIZATION_URL"].strip(),
        values["OIDC_TOKEN_URL"].strip(),
        values["OIDC_CLIENT_ID"].strip(),
        values.get("OIDC_CLIENT_SCOPES", "openid profile").strip(),
        values["SERVER_API_URL"].strip().rstrip("/"),
        values.get("OIDC_AUTHORIZATION_AUDIENCE", "").strip() or None,
        _redirect_port(values.get("OIDC_REDIRECT_PORT", "8765")),
    )


def create_pkce() -> tuple[str, str]:
    verifier = token_urlsafe(64)
    challenge = urlsafe_b64encode(sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorization_url(settings: ConnectSettings, redirect_uri: str, state: str, challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": redirect_uri,
        "scope": settings.scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if settings.audience:
        params["audience"] = settings.audience
    separator = "&" if "?" in settings.authorization_url else "?"
    return f"{settings.authorization_url}{separator}{urlencode(params)}"


def connect(settings: ConnectSettings, display_name: str, session, credential_store, browser_open=webbrowser.open) -> str:
    verifier, challenge = create_pkce()
    state = token_urlsafe(32)
    callbacks: Queue[dict[str, list[str]]] = Queue()
    handler = _callback_handler(callbacks)
    server = ThreadingHTTPServer(("127.0.0.1", settings.redirect_port), handler)
    server.timeout = 1
    redirect_uri = f"http://127.0.0.1:{server.server_port}/callback"
    try:
        if not browser_open(authorization_url(settings, redirect_uri, state, challenge)):
            raise RuntimeError("브라우저를 열 수 없습니다.")
        query = _wait_for_callback(server, callbacks, state, timeout_seconds=180)
    finally:
        server.server_close()

    code = query["code"][0]
    token_response = _post(session, settings.token_url, data={
        "grant_type": "authorization_code",
        "client_id": settings.client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }, timeout=15)
    web_token = _bearer_access_token(token_response.json())
    headers = {"Authorization": f"Bearer {web_token}"}
    registered = _post(session, f"{settings.server_url}/v1/devices", json={"display_name": display_name},
                              headers=headers, timeout=15)
    device_id = str(UUID(_required_string(registered.json(), "device_id")))
    device_url = f"{settings.server_url}/v1/devices/{device_id}"
    try:
        issued = _post(session, f"{device_url}/credentials", headers=headers, timeout=15)
        data = issued.json()
        if _required_string(data, "device_id") != device_id:
            raise ValueError("발급 응답의 기기 ID가 등록한 기기와 다릅니다.")
        credentials = StoredDeviceCredentials(
            server_url=settings.server_url,
            device_id=device_id,
            access_token=_bearer_access_token(data),
            access_expires_at=_required_string(data, "access_expires_at"),
            refresh_token=_required_string(data, "refresh_token"),
            refresh_expires_at=_required_string(data, "refresh_expires_at"),
        )
        credential_store.save(credentials)
    except Exception:
        try:
            session.delete(device_url, headers=headers, timeout=15, allow_redirects=False)
        except Exception:
            pass
        raise
    return device_id


def _post(session, url, **kwargs):
    response = session.post(url, allow_redirects=False, **kwargs)
    if not 200 <= response.status_code < 300:
        raise ValueError("연결 요청이 성공하지 않았습니다.")
    return response


def _wait_for_callback(server, callbacks, expected_state: str, timeout_seconds: int):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        server.handle_request()
        try:
            query = callbacks.get_nowait()
        except Empty:
            continue
        if query.get("state") != [expected_state]:
            continue
        if "error" in query or len(query.get("code", [])) != 1 or not query["code"][0]:
            raise ValueError("인증 제공자가 로그인을 완료하지 않았습니다.")
        return query
    raise TimeoutError("로그인 대기 시간이 끝났습니다.")


def _callback_handler(callbacks):
    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path != "/callback":
                self.send_error(404)
                return
            callbacks.put(parse_qs(parsed.query, keep_blank_values=True))
            body = "로그인 결과를 확인했습니다. 이 창을 닫아도 됩니다.".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *args):
            return

    return Handler


def _required_string(data, name):
    value = data.get(name) if isinstance(data, dict) else None
    if not isinstance(value, str) or not value:
        raise ValueError("서버 응답에 필수 값이 없습니다.")
    return value


def _bearer_access_token(data):
    token_type = _required_string(data, "token_type")
    if token_type.lower() != "bearer":
        raise ValueError("지원하지 않는 token_type입니다.")
    return _required_string(data, "access_token")


def _redirect_port(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError("OIDC_REDIRECT_PORT는 정수여야 합니다.") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description="중앙 서버 계정에 이 로컬 설치 연결")
    parser.add_argument("--device-name", required=True)
    args = parser.parse_args(argv)
    try:
        import requests

        device_id = connect(load_settings(), args.device_name, requests.Session(), KeyringCredentialStore())
        print(f"서버 연결 완료: device_id={device_id}")
        return 0
    except Exception:
        print("SERVER_CONNECT_FAILED: 로그인·서버 설정·OS 자격 증명 저장소를 확인하세요.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
