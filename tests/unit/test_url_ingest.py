import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

from solo_leveling.infrastructure.parsing.url_ingest import (
    UnsafeUrlError,
    classify_content,
    extract_html_text,
    validate_public_url,
)


# ── validate_public_url ──────────────────────────────────────────
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/paper.pdf",
        "http://127.0.0.1/paper.pdf",
        "http://0.0.0.0/paper.pdf",
        "http://10.0.0.5/paper.pdf",
        "http://192.168.1.1/paper.pdf",
        "http://169.254.169.254/latest/meta-data",  # 클라우드 메타데이터 엔드포인트
        "ftp://example.com/paper.pdf",
        "file:///etc/passwd",
    ],
)
def test_validate_public_url_rejects_unsafe(url):
    with pytest.raises(UnsafeUrlError):
        validate_public_url(url)


@pytest.mark.parametrize("url", ["https://arxiv.org/abs/1706.03762", "http://example.com/paper"])
def test_validate_public_url_allows_public_https(url):
    validate_public_url(url)  # 예외 없이 통과해야 함


# ── classify_content ──────────────────────────────────────────────
def _fake_response(content_type="", content=b"", text=""):
    resp = MagicMock()
    resp.headers = {"Content-Type": content_type}
    resp.content = content
    resp.text = text
    return resp


def test_classify_content_type_header_pdf():
    resp = _fake_response(content_type="application/pdf", content=b"%PDF-1.4...")
    assert classify_content(resp, "https://example.com/x") == "pdf"


def test_classify_content_magic_bytes_pdf_without_header():
    resp = _fake_response(content_type="application/octet-stream", content=b"%PDF-1.4 rest")
    assert classify_content(resp, "https://example.com/x") == "pdf"


def test_classify_content_html():
    resp = _fake_response(content_type="text/html; charset=utf-8", content=b"<html>hi</html>", text="<html>hi</html>")
    assert classify_content(resp, "https://example.com/page") == "html"


def test_classify_content_other():
    resp = _fake_response(content_type="application/json", content=b'{"a":1}', text='{"a":1}')
    assert classify_content(resp, "https://example.com/x") == "other"


# ── extract_html_text ───────────────────────────────────────────
def test_extract_html_text_strips_tags_and_scripts():
    html = """
    <html><head><script>alert('x')</script><style>.a{color:red}</style></head>
    <body><h1>Title</h1><p>Body paragraph text.</p></body></html>
    """
    text = extract_html_text(html)
    assert "Title" in text
    assert "Body paragraph text." in text
    assert "alert" not in text
    assert "color:red" not in text
    assert "<" not in text
