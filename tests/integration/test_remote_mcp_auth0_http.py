"""실제 FastAPI에 결합된 원격 MCP Auth0·호스트 경계를 검증한다."""

import time

from fastapi.testclient import TestClient
from mcp.server.auth.provider import AccessToken

from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.remote_mcp import TOOLS
from solo_leveling.interfaces.http.settings import RemoteMcpSettings


RESOURCE = "https://solo-leveling-staging.soogyung.com/mcp"
ISSUER = "https://identity.example.com/"
SETTINGS = RemoteMcpSettings(
    enabled=True,
    resource_url=RESOURCE,
    issuer=ISSUER,
    audience=RESOURCE,
    jwks_url="https://identity.example.com/jwks",
    required_scope="spike:read",
    allowed_hosts=("solo-leveling-staging.soogyung.com",),
    allowed_origins=("https://claude.ai",),
)


class FakeVerifier:
    async def verify_token(self, token):
        if token != "valid":
            return None
        return AccessToken(
            token=token,
            client_id="published-client",
            subject="private-subject",
            resource=RESOURCE,
            scopes=["spike:read"],
            expires_at=int(time.time()) + 600,
        )


def rpc(client, method, params=None, token="valid", **headers):
    return client.post("/mcp", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-03-26",
        **headers,
    }, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def client():
    return TestClient(
        create_app(remote_mcp_settings=SETTINGS, remote_mcp_verifier=FakeVerifier()),
        base_url="https://solo-leveling-staging.soogyung.com",
    )


def test_feature_flag_off_keeps_remote_mcp_unpublished():
    with TestClient(create_app(remote_mcp_settings=RemoteMcpSettings())) as disabled:
        assert disabled.post("/mcp").status_code == 404


def test_metadata_authentication_and_tool_allowlist():
    with client() as value:
        metadata = value.get("/.well-known/oauth-protected-resource/mcp")
        assert metadata.status_code == 200
        assert metadata.json()["resource"] == RESOURCE
        assert metadata.json()["authorization_servers"] == [ISSUER]
        denied = value.post("/mcp")
        assert denied.status_code == 401
        assert "resource_metadata=" in denied.headers["www-authenticate"]
        initialized = rpc(value, "initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "auth0-spike", "version": "1"},
        })
        assert initialized.status_code == 200
        listed = rpc(value, "tools/list").json()["result"]["tools"]
        assert {tool["name"] for tool in listed} == TOOLS
        identity = rpc(value, "tools/call", {"name": "whoami"}).json()["result"]
        assert identity["structuredContent"] == {"authenticated": True, "environment": "staging-spike"}
        assert "private-subject" not in str(identity)
        denied_tool = rpc(value, "tools/call", {"name": "add_paper", "arguments": {}}).json()
        assert denied_tool["result"]["isError"] is True


def test_public_host_allowlist_prevents_421_and_rejects_unknown_hosts():
    with client() as value:
        assert rpc(value, "tools/list").status_code == 200
        rejected = rpc(value, "tools/list", Host="attacker.example")
        assert rejected.status_code == 421
        assert rejected.text == "Invalid Host header"


def test_origin_and_stateless_session_boundaries():
    with client() as value:
        assert rpc(value, "tools/list", Origin="https://claude.ai").status_code == 200
        assert rpc(value, "tools/list", Origin="https://attacker.example").status_code == 403
        session = rpc(value, "tools/list", **{"Mcp-Session-Id": "another-user-session"})
        assert session.status_code == 400
        assert session.json() == {"error": "spike_sessions_not_supported"}


def test_existing_health_and_api_routes_remain_ahead_of_static_fallback():
    with client() as value:
        assert value.get("/health").json()["status"] == "ok"
        assert value.get("/v1/session").status_code == 401
