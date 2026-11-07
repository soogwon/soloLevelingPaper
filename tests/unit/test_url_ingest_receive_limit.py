"""#199 URL PDF 수신 단계의 설정 상한을 검증한다."""
from collections import deque
from http.client import HTTPResponse
import io
from unittest.mock import Mock

import pytest
import requests
from requests.adapters import BaseAdapter
from urllib3.response import HTTPResponse as Urllib3Response

from solo_leveling.infrastructure.parsing import url_ingest


class Response:
    max_piece_size = None

    def __init__(self, body=b'', *, url='https://example.org/paper.pdf', headers=None,
                 redirect=False, location=None, status_error=None):
        self.body = body
        self.url = url
        self.headers = dict(headers or {})
        if location is not None:
            self.headers['Location'] = location
        self.is_redirect = redirect
        self.is_permanent_redirect = False
        self.closed = False
        self.yielded = 0
        self.raw = self
        self.offset = 0
        self.chunk_sizes = []
        self.status_error = status_error

    @property
    def content(self):
        return getattr(self, '_content', self.body)

    @property
    def text(self):
        return self.content.decode('utf-8', errors='replace')

    def raise_for_status(self):
        if self.status_error is not None:
            raise self.status_error

    def close(self):
        self.closed = True

    def read(self, size, *, decode_content):
        self.chunk_sizes.append(size)
        if self.max_piece_size is not None:
            size = min(size, self.max_piece_size)
        block = self.body[self.offset:self.offset + size]
        self.offset += len(block)
        self.yielded += len(block)
        return block


class Session:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.cookies = requests.cookies.RequestsCookieJar()
        self.urls = []
        self.closed = False

    def prepare_request(self, request):
        return request.prepare()

    def merge_environment_settings(self, url, proxies, stream, verify, cert):
        return {'proxies': {}, 'verify': True, 'cert': None}

    def get_adapter(self, url):
        return self

    def send(self, request, **kwargs):
        self.urls.append(request.url)
        return self.responses.popleft()

    def close(self):
        self.closed = True


class SmallPieceResponse(Response):
    """서버가 요청한 chunk_size보다 작은 조각을 반복해서 주는 경우를 흉내 낸다."""

    max_piece_size = 2


class TrackingRaw:
    def __init__(self, body):
        self.body = io.BytesIO(body)
        self.read_bytes = 0
        self.closed = False

    def read(self, amount, *, decode_content):
        block = self.body.read(amount)
        self.read_bytes += len(block)
        return block

    def close(self):
        self.closed = True

    def release_conn(self):
        pass


class QueueAdapter(BaseAdapter):
    def __init__(self, specs):
        self.specs = deque(specs)
        self.responses = []

    def send(self, request, **kwargs):
        status, headers, body = self.specs.popleft()
        response = requests.Response()
        response.status_code = status
        response.headers.update(headers)
        response.url = request.url
        response.request = request
        response.raw = TrackingRaw(body)
        self.responses.append(response)
        return response

    def close(self):
        pass


def install(monkeypatch, *responses):
    session = Session(responses)
    monkeypatch.setattr(url_ingest.requests, 'Session', lambda: session)
    return session


def test_exact_pdf_boundary_is_allowed(monkeypatch):
    response = Response(b'%PDF-12345', headers={'Content-Length': '10', 'Content-Type': 'application/pdf'})
    install(monkeypatch, response)
    fetched = url_ingest.fetch_url(response.url, pdf_max_bytes=10)
    assert fetched.content == b'%PDF-12345' and response.yielded == 10


def test_declared_one_byte_over_is_rejected_before_body(monkeypatch):
    response = Response(b'%PDF-12345x', headers={'Content-Length': '11', 'Content-Type': 'application/pdf'})
    install(monkeypatch, response)
    with pytest.raises(url_ingest.ResponseTooLargeError) as raised:
        url_ingest.fetch_url(response.url, pdf_max_bytes=10)
    assert str(raised.value) == url_ingest.URL_RESPONSE_TOO_LARGE
    assert response.yielded == 0 and response.closed


@pytest.mark.parametrize('headers', [
    {'Content-Type': 'application/pdf'},
    {'Content-Type': 'application/pdf', 'Content-Length': '1'},
    {'Content-Type': 'application/pdf', 'Transfer-Encoding': 'chunked'},
])
def test_missing_forged_or_chunked_length_uses_received_byte_count(monkeypatch, headers):
    response = Response(b'%PDF-12345x', headers=headers)
    install(monkeypatch, response)
    with pytest.raises(url_ingest.ResponseTooLargeError):
        url_ingest.fetch_url(response.url, pdf_max_bytes=10)
    assert response.yielded == 11 and response.closed


def test_pdf_magic_switches_unknown_response_to_pdf_limit(monkeypatch):
    response = Response(b'%PDF-12345x', url='https://example.org/download', headers={})
    install(monkeypatch, response)
    with pytest.raises(url_ingest.ResponseTooLargeError):
        url_ingest.fetch_url(response.url, pdf_max_bytes=10, other_max_bytes=100)
    assert response.yielded == 11


def test_unknown_pdf_magic_allows_exact_pdf_boundary(monkeypatch):
    response = Response(
        b'%PDF-12345', url='https://example.org/download', headers={'Content-Length': '10'})
    install(monkeypatch, response)
    kind, fetched = url_ingest.fetch_and_classify(
        response.url, pdf_max_bytes=10, other_max_bytes=100)
    assert kind == 'pdf' and fetched.content == b'%PDF-12345'
    assert response.yielded == 10


def test_invalid_pdf_magic_is_classified_as_other_and_uses_other_limit(monkeypatch):
    response = Response(b'%PDFx' + b'a' * 75, url='https://example.org/download', headers={})
    install(monkeypatch, response)
    kind, fetched = url_ingest.fetch_and_classify(
        response.url, pdf_max_bytes=10, other_max_bytes=100)
    assert kind == 'other' and fetched.content == response.body


def test_unknown_content_length_waits_for_pdf_magic_before_selecting_limit(monkeypatch):
    response = Response(
        b'%PDF-' + b'a' * 75, url='https://example.org/download', headers={'Content-Length': '80'})
    install(monkeypatch, response)
    kind, fetched = url_ingest.fetch_and_classify(
        response.url, pdf_max_bytes=100, other_max_bytes=20)
    assert kind == 'pdf' and fetched.content == response.body
    assert response.chunk_sizes[0] == 5


def test_unknown_pdf_magic_can_be_split_across_small_chunks(monkeypatch):
    response = SmallPieceResponse(
        b'%PDF-' + b'a' * 75, url='https://example.org/download', headers={'Content-Length': '80'})
    install(monkeypatch, response)
    kind, fetched = url_ingest.fetch_and_classify(
        response.url, pdf_max_bytes=100, other_max_bytes=20)
    assert kind == 'pdf' and fetched.content == response.body
    assert response.chunk_sizes[0] == 5


def test_unknown_pdf_applies_smaller_pdf_limit_after_split_magic(monkeypatch):
    response = Response(
        b'%PDF-' + b'a' * 75, url='https://example.org/download', headers={'Content-Length': '80'})
    install(monkeypatch, response)
    with pytest.raises(url_ingest.ResponseTooLargeError):
        url_ingest.fetch_url(response.url, pdf_max_bytes=10, other_max_bytes=100)
    assert response.chunk_sizes[0] == 5 and response.yielded == 5


def test_pdf_path_with_query_is_rejected_from_content_length_before_body(monkeypatch):
    response = Response(
        b'%PDF-12345x', url='https://example.org/paper.pdf?signature=private',
        headers={'Content-Length': '11'})
    install(monkeypatch, response)
    with pytest.raises(url_ingest.ResponseTooLargeError):
        url_ingest.fetch_url(response.url, pdf_max_bytes=10, other_max_bytes=100)
    assert response.yielded == 5 and response.closed


def test_redirect_final_response_uses_same_pdf_limit(monkeypatch):
    redirect = Response(redirect=True, location='/final.pdf', url='https://example.org/start')
    final = Response(b'%PDF-12345x', url='https://example.org/final.pdf', headers={'Content-Length': '11'})
    session = install(monkeypatch, redirect, final)
    with pytest.raises(url_ingest.ResponseTooLargeError):
        url_ingest.fetch_url(redirect.url, pdf_max_bytes=10)
    assert session.urls == ['https://example.org/start', 'https://example.org/final.pdf']
    assert redirect.closed and final.closed and final.yielded == 5


def test_real_session_does_not_consume_redirect_body(monkeypatch):
    adapter = QueueAdapter([
        (302, {'Location': '/final.pdf'}, b'x' * 1_000_000),
        (200, {'Content-Type': 'application/pdf'}, b'%PDF-12345'),
    ])
    session = requests.Session()
    session.mount('https://', adapter)
    monkeypatch.setattr(url_ingest.requests, 'Session', lambda: session)

    fetched = url_ingest.fetch_url(
        'https://example.org/start', pdf_max_bytes=10, other_max_bytes=20)
    assert fetched.content == b'%PDF-12345'
    assert adapter.responses[0].raw.read_bytes == 0
    assert adapter.responses[0].raw.closed


def test_pdf_path_with_html_body_uses_other_limit(monkeypatch):
    response = Response(
        b'<html>' + b'a' * 74, url='https://example.org/a.pdf',
        headers={'Content-Type': 'text/html'})
    install(monkeypatch, response)
    with pytest.raises(url_ingest.ResponseTooLargeError):
        url_ingest.fetch_url(response.url, pdf_max_bytes=100, other_max_bytes=20)
    assert response.yielded == 21 and response.closed


def test_each_read_stops_at_one_byte_over_remaining_limit(monkeypatch):
    response = Response(
        b'%PDF-' + b'a' * 200_000, headers={'Content-Type': 'application/pdf'})
    install(monkeypatch, response)
    with pytest.raises(url_ingest.ResponseTooLargeError):
        url_ingest.fetch_url(response.url, pdf_max_bytes=65_536)
    assert response.yielded == 65_537 and response.closed


def test_streaming_response_is_closed_on_http_error(monkeypatch):
    response = Response(status_error=RuntimeError('HTTP failure'))
    session = install(monkeypatch, response)
    with pytest.raises(RuntimeError, match='HTTP failure'):
        url_ingest.fetch_url(response.url, pdf_max_bytes=10)
    assert response.closed and session.closed


def test_html_keeps_separate_response_limit(monkeypatch):
    response = Response(b'<html>ok', url='https://example.org/page', headers={'Content-Type': 'text/html'})
    install(monkeypatch, response)
    fetched = url_ingest.fetch_url(response.url, pdf_max_bytes=5, other_max_bytes=20)
    assert fetched.content == b'<html>ok'


@pytest.mark.parametrize('name,value', [('pdf_max_bytes', 0), ('pdf_max_bytes', True), ('other_max_bytes', -1)])
def test_limits_must_be_positive_integers(monkeypatch, name, value):
    monkeypatch.setattr(url_ingest.requests, 'Session', lambda: pytest.fail('invalid limit must fail before session'))
    with pytest.raises(ValueError):
        url_ingest.fetch_url('https://example.org/a.pdf', **{name: value})

@pytest.mark.parametrize('body, content_type, pdf_limit, other_limit, expected_kind', [
    (b'%PDF-12345', '', 10, 20, 'pdf'),
    (b'<html>' + b'a' * 74, '', 100, 20, None),
    (b'%PDF-' + b'a' * 140_000, 'application/pdf', 65_536, 20, None),
], ids=['exact-pdf', 'oversize-html', 'oversize-pdf'])
def test_http_chunked_response_is_consumed_to_the_limit(
        monkeypatch, body, content_type, pdf_limit, other_limit, expected_kind):
    # 실제 HTTP 디코더에 첫 5바이트와 나머지를 별도 청크로 전달한다.
    wire = b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n'
    for part in (body[:5], body[5:]):
        wire += f'{len(part):x}\r\n'.encode() + part + b'\r\n'
    sock = Mock()
    sock.makefile.return_value = io.BytesIO(wire + b'0\r\n\r\n')
    http = HTTPResponse(sock, method='GET')
    http.begin()
    response = requests.Response()
    response.status_code = 200
    response.url = 'https://example.org/a.pdf'
    response.headers.update(http.getheaders())
    if content_type:
        response.headers['Content-Type'] = content_type
    response.raw = Urllib3Response(body=http, original_response=http, headers=response.headers,
                                   preload_content=False, request_method='GET')
    install(monkeypatch, response)
    limits = dict(pdf_max_bytes=pdf_limit, other_max_bytes=other_limit)
    if expected_kind is None:
        with pytest.raises(url_ingest.ResponseTooLargeError, match='^URL_RESPONSE_TOO_LARGE$'):
            url_ingest.fetch_and_classify(response.url, **limits)
        assert response.raw.closed
    else:
        kind, fetched = url_ingest.fetch_and_classify(response.url, **limits)
        assert (kind, fetched.content) == (expected_kind, body)
