"""Auth0나 네트워크 없이 #388 전송 경계만 확인한다."""

import importlib.util
from pathlib import Path
import time

import pytest
from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient


PATH = Path(__file__).resolve().parents[2] / "mini-projects/soogwon/remote-mcp-spike/probe.py"
SPEC = importlib.util.spec_from_file_location("remote_spike_probe", PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
RESOURCE = "https://spike.example/mcp"
ISSUER = "https://issuer.example/"


class FakeVerifier:
    async def verify_token(self, token):
        if token == "provider-error":
            raise RuntimeError("private-provider-details")
        if token == "invalid":
            return None
        return AccessToken(
            token=token, client_id="synthetic-client", subject="private-subject",
            resource="https://web.example/" if token == "wrong-resource" else RESOURCE,
            scopes=[] if token == "wrong-scope" else [probe.SCOPE],
            expires_at=int(time.time()) + (-10 if token == "expired" else 600),
        )


@pytest.fixture
def client():
    app = probe.create_probe(resource_url=RESOURCE, issuer_url=ISSUER, verifier=FakeVerifier())
    with TestClient(app, base_url="http://127.0.0.1:8000") as value:
        yield value


def rpc(client, method, params=None, token="valid", **headers):
    return client.post("/mcp", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-03-26", **headers,
    }, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def test_discovery_and_default_deny():
    with TestClient(probe.create_probe(resource_url=RESOURCE, issuer_url=ISSUER),
                    base_url="http://127.0.0.1:8000") as client:
        denied = rpc(client, "tools/list")
        assert denied.status_code == 401
        assert "resource_metadata=" in denied.headers["www-authenticate"]
        metadata = client.get("/.well-known/oauth-protected-resource/mcp").json()
        assert metadata["resource"] == RESOURCE
        assert metadata["authorization_servers"] == [ISSUER]


@pytest.mark.parametrize("token,status", [
    ("invalid", 401), ("expired", 401), ("wrong-resource", 401),
    ("wrong-scope", 403), ("provider-error", 401),
])
def test_rejected_credentials(client, token, status, caplog):
    response = rpc(client, "tools/list", token=token)
    assert response.status_code == status
    for secret in ("private-provider-details", "private-subject", f"Bearer {token}"):
        assert secret not in response.text + caplog.text


def test_no_token(client):
    assert client.post("/mcp").status_code == 401


def test_initialize_allowlist_and_private_identity(client):
    initialized = rpc(client, "initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "synthetic-test", "version": "1"}})
    assert initialized.status_code == 200
    assert "mcp-session-id" not in initialized.headers
    listed = rpc(client, "tools/list").json()["result"]["tools"]
    assert {tool["name"] for tool in listed} == probe.TOOLS
    identity = rpc(client, "tools/call", {"name": "whoami"}).json()["result"]
    assert identity["structuredContent"]["authenticated"] is True
    assert "private-subject" not in str(identity)
    denied = rpc(client, "tools/call", {"name": "add_paper", "arguments": {}}).json()
    assert denied["result"]["isError"] is True


def test_card_resource_and_text_fallback(client):
    tools = rpc(client, "tools/list").json()["result"]["tools"]
    card = next(tool for tool in tools if tool["name"] == "probe_card")
    assert card["_meta"]["ui"]["resourceUri"] == probe.CARD_URI
    result = rpc(client, "tools/call", {"name": "probe_card"}).json()["result"]
    assert result["content"][0]["type"] == "text"
    resource = rpc(client, "resources/read", {"uri": probe.CARD_URI}).json()["result"]
    assert resource["contents"][0]["mimeType"] == "text/html;profile=mcp-app"
    assert "원격 연결 합성 카드" in resource["contents"][0]["text"]


def test_delay_limit_and_no_sessions(client):
    result = rpc(client, "tools/call", {"name": "probe_delay", "arguments": {"seconds": 0.01}})
    assert result.json()["result"]["structuredContent"]["completed"] is True
    for seconds in (-1, 6):
        result = rpc(client, "tools/call", {"name": "probe_delay", "arguments": {"seconds": seconds}})
        assert result.json()["result"]["isError"] is True
    response = rpc(client, "tools/list", **{"Mcp-Session-Id": "another-account-session"})
    assert response.status_code == 400


@pytest.mark.parametrize("resource", ["http://spike.example/mcp", "https://u:p@spike.example/mcp",
                                      "https://spike.example/mcp?token=secret", "https://spike.example/other"])
def test_invalid_resource_configuration(resource):
    with pytest.raises(ValueError):
        probe.create_probe(resource_url=resource, issuer_url=ISSUER)
