"""
infrastructure/parsing/url_ingest — URL 등록(M1 기능 ①의 URL 부분)

PDF 링크면 기존 PDF 파이프라인으로 넘기고, 일반 웹페이지면 본문 텍스트를
추출해 "페이지 1개짜리 논문"처럼 취급한다.

보안 주의(팀 공유용): 여기서는 사설망 접근을 막는 최소한의 검증만 넣었다.
04번 문서에서 "URL 수집의 사설망 접근을 차단"은 C 담당 영역으로 명시돼 있어,
C의 최종 보안 검증 로직과 반드시 합쳐서 검토해야 한다 — 이건 그 전까지
A가 URL 파이프라인을 개발·테스트할 수 있게 하는 임시 최소 방어선이다.
"""
import ipaddress
import re
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests

USER_AGENT = "solo-leveling-paper/0.1 (URL registration)"
REQUEST_TIMEOUT = (5, 30)

_PRIVATE_HOSTNAMES = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


class UnsafeUrlError(ValueError):
    """사설망·비HTTP(S) URL 등 등록을 거부해야 하는 경우"""


def _is_private_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # 호스트네임(도메인)이면 여기서 판단 안 함 — DNS 재바인딩 방어는 C 영역
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def validate_public_url(url: str) -> None:
    """
    사설망·로컬·비HTTP(S) URL을 거부한다.
    주의: 이건 최소 방어선이다. DNS 리바인딩, 리다이렉트 체인을 통한 우회까지
    막으려면 C의 정식 네트워크 정책과 통합해야 한다(04번 문서).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"http/https만 허용됩니다: {url}")
    if not parsed.hostname:
        raise UnsafeUrlError(f"호스트를 확인할 수 없습니다: {url}")
    if parsed.hostname.lower() in _PRIVATE_HOSTNAMES:
        raise UnsafeUrlError(f"사설/로컬 주소는 등록할 수 없습니다: {url}")
    if _is_private_ip(parsed.hostname):
        raise UnsafeUrlError(f"사설 IP 대역은 등록할 수 없습니다: {url}")


def fetch_url(url: str) -> requests.Response:
    validate_public_url(url)
    response = requests.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT, allow_redirects=True
    )
    response.raise_for_status()
    return response


def classify_content(response: requests.Response, url: str) -> str:
    """반환: "pdf" | "html" | "other" """
    content_type = response.headers.get("Content-Type", "").lower()
    if "application/pdf" in content_type:
        return "pdf"
    if url.lower().endswith(".pdf") and response.content[:4] == b"%PDF":
        return "pdf"
    if response.content[:4] == b"%PDF":
        return "pdf"
    if "text/html" in content_type or "<html" in response.text[:1000].lower():
        return "html"
    return "other"


_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")


def extract_html_text(html: str) -> str:
    """
    아주 단순한 태그 제거 기반 본문 추출.
    정교한 본문 판별(광고·네비게이션 제거 등)은 범위 밖 — 필요하면
    beautifulsoup4 같은 라이브러리로 교체 가능하도록 이 함수만 바꾸면 된다.
    """
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub("\n", text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = _BLANKLINES_RE.sub("\n\n", text)
    return text.strip()


def fetch_and_classify(url: str) -> Tuple[str, requests.Response]:
    """URL을 가져와 (kind, response) 반환. kind: "pdf" | "html" | "other" """
    response = fetch_url(url)
    kind = classify_content(response, url)
    return kind, response
