"""인증된 기기로 웹의 이어하기 코드를 조회하고 응답 계약을 검증한다."""

from typing import Any

from solo_leveling.infrastructure.storage.server_client import ServerResponseInvalid


class ResumeRequestUnavailable(Exception):
    pass


class ResumeRequestExpired(Exception):
    pass


class ResumeRequestResolver:
    def __init__(self, client: Any):
        self._client = client

    def resolve(self, code: str) -> dict[str, Any]:
        response = self._client.request(
            "POST", "/v1/mcp/resume-requests/resolve", json={"code": code}, timeout=15,
        )
        try:
            if response.status_code == 404:
                raise ResumeRequestUnavailable
            if response.status_code == 410:
                raise ResumeRequestExpired
            if not 200 <= response.status_code < 300:
                response.raise_for_status()
            try:
                data = response.json()
            except ValueError:
                raise ServerResponseInvalid from None
        finally:
            response.close()
        if not _valid(data):
            raise ServerResponseInvalid
        return data


def _valid(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    required = ("resume_request_id", "paper_id", "version_id", "learning_record_id",
                "file_access_status", "status")
    if any(not isinstance(data.get(key), str) or not data[key] for key in required):
        return False
    if data.get("title") is not None and not isinstance(data["title"], str):
        return False
    if data.get("file_name") is not None and not isinstance(data["file_name"], str):
        return False
    if data.get("last_position") is not None and not isinstance(data["last_position"], dict):
        return False
    if data.get("annotation") is not None and not isinstance(data["annotation"], dict):
        return False
    return True
