"""네트워크 발급 없이 #388 PKCE 검증기의 로컬 경계를 확인한다."""

import importlib.util
from pathlib import Path

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
    assert result == {"error": "access_denied"}
