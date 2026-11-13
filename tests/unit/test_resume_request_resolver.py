import pytest

from solo_leveling.application.server_sync.resume import (
    ResumeRequestExpired, ResumeRequestResolver, ResumeRequestUnavailable,
)
from solo_leveling.infrastructure.storage.server_client import ServerResponseInvalid


class Response:
    def __init__(self, status, data=None):
        self.status_code = status
        self.data = data
        self.closed = False

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data

    def close(self):
        self.closed = True

    def raise_for_status(self):
        raise RuntimeError("server failure")


class Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


def payload(**change):
    return {
        "resume_request_id": "request-1", "paper_id": "paper-1", "title": "Paper",
        "version_id": "version-1", "learning_record_id": "record-1",
        "last_position": {"pdf_page": 3}, "annotation_id": None, "annotation": None,
        "file_name": "paper.pdf", "file_access_status": "local_check_required",
        "status": "prepared", "reason": "local_file_required",
    } | change


def test_resolver_posts_code_and_returns_validated_selection():
    response = Response(200, payload())
    client = Client(response)
    result = ResumeRequestResolver(client).resolve("resume_code")
    assert result["last_position"] == {"pdf_page": 3}
    assert client.calls == [(('POST', '/v1/mcp/resume-requests/resolve'), {
        'json': {'code': 'resume_code'}, 'timeout': 15,
    })]
    assert response.closed


@pytest.mark.parametrize("status,error", [
    (404, ResumeRequestUnavailable), (410, ResumeRequestExpired),
])
def test_resolver_distinguishes_missing_and_expired(status, error):
    response = Response(status)
    with pytest.raises(error):
        ResumeRequestResolver(Client(response)).resolve("resume_code")
    assert response.closed


@pytest.mark.parametrize("data", [None, {}, payload(status=None), payload(last_position="page 3")])
def test_resolver_rejects_untrusted_server_shapes(data):
    response = Response(200, data)
    with pytest.raises(ServerResponseInvalid):
        ResumeRequestResolver(Client(response)).resolve("resume_code")
    assert response.closed
