"""네트워크 발급 없이 #388 PKCE 검증기의 로컬 경계를 확인한다."""

import importlib.util
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import requests

PATH = Path(__file__).resolve().parents[2] / "scripts/verify_remote_mcp_auth0.py"
SPEC = importlib.util.spec_from_file_location("issue_388_auth0_verifier", PATH)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def test_b64url_is_unpadded_and_url_safe():
    assert verifier._b64url(b"\xfb\xff") == "-_8"


def test_callback_accepts_matching_state_and_keeps_only_code():
    status, message, result = verifier._parse_callback(
        "/callback?state=expected&code=temporary-code",
        "expected",
    )

    assert status == 200
    assert message == "Authorization received. You can close this tab."
    assert result == {"code": "temporary-code"}


def test_callback_rejects_state_mismatch_without_recording_code():
    status, _, result = verifier._parse_callback(
        "/callback?state=attacker&code=temporary-code",
        "expected",
    )

    assert status == 400
    assert result == {}


def test_callback_records_only_safe_oauth_error_name():
    status, message, result = verifier._parse_callback(
        "/callback?state=expected&error=access_denied&error_description=private-details",
        "expected",
    )

    assert status == 400
    assert "private-details" not in message
    assert result == {"error": "authorization_rejected"}


@pytest.mark.parametrize("path", [
    "/favicon.ico?state=expected&code=secret",
    "/callback?state=expected&state=other&code=secret",
    "/callback?state=expected&code=first&code=second",
    "/callback?state=expected&code=",
])
def test_callback_rejects_wrong_path_or_ambiguous_fields(path):
    assert verifier._parse_callback(path, "expected")[2] == {}


def test_callback_does_not_echo_arbitrary_error():
    result = verifier._parse_callback("/callback?state=expected&error=private-token", "expected")
    assert "private-token" not in str(result)


def test_callback_waits_after_unrelated_request_and_keeps_first_valid_code():
    result = {}
    server = verifier._callback_server(0, "expected", result)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        assert requests.get(url + "/favicon.ico", timeout=5).status_code == 404
        assert requests.get(url + "/callback?state=wrong&code=wrong", timeout=5).status_code == 400
        assert not server.callback_received.is_set()
        assert requests.get(url + "/callback?state=expected&code=first", timeout=5).status_code == 200
        assert server.callback_received.wait(1)
        requests.get(url + "/callback?state=expected&code=second", timeout=5)
        assert result == {"code": "first"}
    finally:
        server.shutdown()
        worker.join()
        server.server_close()


def response(status=200, result=None, *, payload=None):
    value = requests.Response()
    value.status_code = status
    value._content = json.dumps(payload if payload is not None else {
        "jsonrpc": "2.0", "id": 1, "result": result or {}
    }).encode()
    return value


def arrange_main(monkeypatch, replies, expect="allowed"):
    monkeypatch.setattr("sys.argv", ["verify", "--client-id", "synthetic", "--expect", expect])

    def callback_server(port, state, result):
        result["code"] = "synthetic-code"
        event = threading.Event()
        event.set()
        return SimpleNamespace(callback_received=event, serve_forever=lambda: None,
                               shutdown=lambda: None, server_close=lambda: None)

    monkeypatch.setattr(verifier, "_callback_server", callback_server)
    monkeypatch.setattr(verifier.requests, "post", lambda *a, **kw: response(payload={"access_token": "private-token"}))
    iterator = iter(replies)
    monkeypatch.setattr(verifier, "_rpc", lambda *a, **kw: next(iterator))


def successful_replies():
    return [response(result={"protocolVersion": "2025-03-26"}),
            response(result={"tools": [{"name": "whoami"}]}),
            response(result={"content": [{"type": "text", "text": "synthetic"}]}),
            response(400), response(result={"isError": True})]


def test_main_requires_all_positive_and_negative_checks(monkeypatch, capsys):
    arrange_main(monkeypatch, successful_replies())
    assert verifier.main() == 0
    assert "private-token" not in capsys.readouterr().out


@pytest.mark.parametrize("index,replacement", [
    (0, response(payload={"jsonrpc": "2.0", "id": 1, "error": {"code": -1}})),
    (1, response(result={"tools": []})),
    (2, response(result={"isError": True, "content": []})),
    (3, response(200)),
    (4, response(result={"isError": False})),
])
def test_initialize_200_does_not_hide_failed_checks(monkeypatch, index, replacement):
    replies = successful_replies()
    replies[index] = replacement
    arrange_main(monkeypatch, replies)
    assert verifier.main() == 4


def test_failed_http_status_is_preserved(monkeypatch, capsys):
    replies = successful_replies()
    replies[1] = response(403)
    del replies[2]
    arrange_main(monkeypatch, replies)
    assert verifier.main() == 4
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["tools_list_status"] == 403


def test_expected_denial_succeeds_without_followup_calls(monkeypatch):
    arrange_main(monkeypatch, [response(401)], expect="denied")
    assert verifier.main() == 0


def test_network_error_detail_is_not_printed(monkeypatch, capsys):
    def failed():
        raise requests.RequestException("private-token-and-body")
    monkeypatch.setattr(verifier, "_main", failed)
    assert verifier.main() == 5
    assert "private-token-and-body" not in capsys.readouterr().out
