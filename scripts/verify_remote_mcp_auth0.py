"""Issue #388 Auth0 PKCE and remote MCP verification without persisting tokens."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests


ISSUER = "https://dev-yqpelssse77yfobe.us.auth0.com"
RESOURCE = "https://solo-leveling-staging.soogyung.com/mcp"
STAGING_API = "https://solo-leveling-staging.soogyung.com/api"
MANAGEMENT_API = f"{ISSUER}/api/v2/"
SCOPES = {
    "full": "openid profile email spike:read",
    "no-spike": "openid profile email",
}


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _parse_callback(path: str, expected_state: str) -> tuple[int, str, dict[str, str]]:
    query = parse_qs(urlparse(path).query)
    if query.get("state", [""])[0] != expected_state:
        return 400, "OAuth state mismatch. You can close this tab.", {}
    if "error" in query:
        return 400, "Authorization was rejected. You can close this tab.", {"error": query["error"][0]}
    if "code" not in query:
        return 400, "Authorization code missing. You can close this tab.", {}
    return 200, "Authorization received. You can close this tab.", {"code": query["code"][0]}


def _callback_server(port: int, expected_state: str, result: dict[str, str]) -> HTTPServer:
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            status, message, callback = _parse_callback(self.path, expected_state)
            result.update(callback)
            self.send_response(status)
            body = message.encode("utf-8")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return HTTPServer(("127.0.0.1", port), CallbackHandler)


def _rpc(
    token: str,
    method: str,
    params: dict | None = None,
    extra_headers: dict[str, str] | None = None,
) -> requests.Response:
    return requests.post(
        RESOURCE,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-03-26",
            **(extra_headers or {}),
        },
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        timeout=30,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--expect", choices=("allowed", "denied"), required=True)
    parser.add_argument("--requested-scope", choices=SCOPES, default="full")
    parser.add_argument("--audience", choices=("mcp", "staging-api", "management-api"), default="mcp")
    parser.add_argument("--token-kind", choices=("access", "id"), default="access")
    args = parser.parse_args()

    redirect_uri = f"http://127.0.0.1:{args.port}/callback"
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_urlsafe(32)
    audience = {
        "mcp": RESOURCE,
        "staging-api": STAGING_API,
        "management-api": MANAGEMENT_API,
    }[args.audience]
    authorize_url = f"{ISSUER}/authorize?" + urlencode(
        {
            "response_type": "code",
            "client_id": args.client_id,
            "redirect_uri": redirect_uri,
            "audience": audience,
            "scope": SCOPES[args.requested_scope],
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "prompt": "login",
        }
    )

    callback: dict[str, str] = {}
    server = _callback_server(args.port, state, callback)
    worker = threading.Thread(target=server.handle_request, daemon=True)
    worker.start()
    print(json.dumps({"authorize_url": authorize_url, "token_persisted": False}))
    worker.join(timeout=300)
    server.server_close()
    if worker.is_alive() or "code" not in callback:
        print(json.dumps({"authorization_received": False, "error": callback.get("error", "timeout")}))
        return 2

    token_response = requests.post(
        f"{ISSUER}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": args.client_id,
            "code": callback["code"],
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    if token_response.status_code != 200:
        print(json.dumps({"authorization_received": True, "token_status": token_response.status_code}))
        return 3
    token_payload = token_response.json()
    token = token_payload.get("access_token" if args.token_kind == "access" else "id_token")
    if not isinstance(token, str) or not token:
        print(json.dumps({"authorization_received": True, "credential_received": False}))
        return 3

    initialized = _rpc(
        token,
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "issue-388-verifier", "version": "1"},
        },
    )
    listed = _rpc(token, "tools/list") if initialized.status_code == 200 else None
    identity = _rpc(token, "tools/call", {"name": "whoami", "arguments": {}}) if listed and listed.status_code == 200 else None
    session_rejected = None
    denied_tool_is_error = None
    if initialized.status_code == 200:
        session_rejected = _rpc(
            token,
            "tools/list",
            extra_headers={"Mcp-Session-Id": "synthetic-session"},
        ).status_code
        denied_tool = _rpc(token, "tools/call", {"name": "not_allowed_probe", "arguments": {}})
        if denied_tool.status_code == 200:
            denied_tool_is_error = denied_tool.json().get("result", {}).get("isError") is True
    statuses = {
        "authorization_received": True,
        "credential_received": True,
        "requested_scope": args.requested_scope,
        "requested_audience": args.audience,
        "token_kind": args.token_kind,
        "initialize_status": initialized.status_code,
        "tools_list_status": listed.status_code if listed else None,
        "whoami_status": identity.status_code if identity else None,
        "synthetic_session_status": session_rejected,
        "denied_tool_is_error": denied_tool_is_error,
        "token_persisted": False,
    }
    print(json.dumps(statuses))
    expected = 200 if args.expect == "allowed" else 401
    return 0 if initialized.status_code == expected else 4


if __name__ == "__main__":
    raise SystemExit(main())
